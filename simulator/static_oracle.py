from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List

from .models import SimulationConfig, Task, Vehicle
from .simulation import ScenarioData


@dataclass
class OracleResult:
    score: float
    completed: int
    unserved: int
    overtime: int
    task_count: int
    runtime_sec: float
    expanded_nodes: int
    total_distance: float
    avg_response_time: float
    plan: List[tuple[int, int, float, float, float, float]]


@dataclass
class _VehicleState:
    vehicle_id: int
    available_time: float
    battery: float
    current_node: int


def solve_static_oracle_bnb(
    scenario: ScenarioData,
    task_limit: int = 11,
    vehicle_limit: int = 4,
    node_limit: int = 350000,
) -> OracleResult:
    start_time = time.time()

    tasks = sorted(
        scenario.tasks,
        key=lambda t: (t.release_time, t.deadline, -t.weight),
    )[:task_limit]
    tasks = sorted(tasks, key=lambda t: (t.deadline, t.release_time, -t.weight, t.task_id))
    task_by_id = {task.task_id: task for task in tasks}

    vehicle_ids = sorted(scenario.vehicles.keys())[:vehicle_limit]
    vehicles = {vehicle_id: scenario.vehicles[vehicle_id] for vehicle_id in vehicle_ids}

    initial_states = {
        vehicle_id: _VehicleState(
            vehicle_id=vehicle_id,
            available_time=0.0,
            battery=vehicles[vehicle_id].battery,
            current_node=vehicles[vehicle_id].current_node,
        )
        for vehicle_id in vehicle_ids
    }

    best_score = float("-inf")
    best_plan: List[tuple[int, int, float, float, float, float]] = []
    expanded_nodes = 0

    ideal_upper_per_task = [240.0 + 6.0 * task.weight for task in tasks]

    def branch(
        remaining: List[int],
        states: Dict[int, _VehicleState],
        cur_score: float,
        cur_plan: List[tuple[int, int, float, float, float, float]],
    ) -> None:
        nonlocal best_score, best_plan, expanded_nodes

        if expanded_nodes >= node_limit:
            return
        expanded_nodes += 1

        if not remaining:
            if cur_score > best_score:
                best_score = cur_score
                best_plan = list(cur_plan)
            return

        optimistic = cur_score + sum(ideal_upper_per_task[idx] for idx in remaining)
        if optimistic <= best_score:
            return

        task_idx = remaining[0]
        task = tasks[task_idx]
        next_remaining = remaining[1:]

        # Branch A: leave task unserved
        branch(
            next_remaining,
            states,
            cur_score - scenario.config.unserved_penalty,
            cur_plan,
        )

        # Branch B: assign to each vehicle
        for vehicle_id, state in states.items():
            mission = _simulate_single_vehicle_mission(
                task=task,
                state=state,
                vehicle=vehicles[vehicle_id],
                config=scenario.config,
                scenario=scenario,
            )
            if mission is None:
                continue

            completion, distance, wait, final_battery, final_node = mission
            response_time = completion - task.release_time
            overtime = completion > task.deadline
            reward = _score_task(
                response_time=response_time,
                distance=distance,
                charging_wait=wait,
                overtime=overtime,
                overtime_penalty=scenario.config.overtime_penalty,
            )

            new_states = {k: _VehicleState(**vars(v)) for k, v in states.items()}
            new_states[vehicle_id] = _VehicleState(
                vehicle_id=vehicle_id,
                available_time=completion,
                battery=max(0.0, final_battery),
                current_node=final_node,
            )

            cur_plan.append((task.task_id, vehicle_id, completion, reward, response_time, distance))
            branch(
                next_remaining,
                new_states,
                cur_score + reward,
                cur_plan,
            )
            cur_plan.pop()

    branch(
        remaining=list(range(len(tasks))),
        states=initial_states,
        cur_score=0.0,
        cur_plan=[],
    )

    completed = len(best_plan)
    overtime = 0
    for task_id, _, completion, _, _, _ in best_plan:
        task = task_by_id[task_id]
        if completion > task.deadline:
            overtime += 1

    total_distance = sum(item[5] for item in best_plan)
    avg_response_time = sum(item[4] for item in best_plan) / max(1, len(best_plan))
    runtime_sec = time.time() - start_time

    return OracleResult(
        score=best_score,
        completed=completed,
        unserved=len(tasks) - completed,
        overtime=overtime,
        task_count=len(tasks),
        runtime_sec=runtime_sec,
        expanded_nodes=expanded_nodes,
        total_distance=total_distance,
        avg_response_time=avg_response_time,
        plan=best_plan,
    )


def _simulate_single_vehicle_mission(
    task: Task,
    state: _VehicleState,
    vehicle: Vehicle,
    config: SimulationConfig,
    scenario: ScenarioData,
) -> tuple[float, float, float, float, int] | None:
    dispatch_time = max(state.available_time, float(task.release_time))

    d_start_task, _ = scenario.graph.shortest_path(state.current_node, task.node_id)
    d_task_depot, _ = scenario.graph.shortest_path(task.node_id, config.depot_node)
    if d_start_task == float("inf") or d_task_depot == float("inf"):
        return None

    direct_energy_need = (d_start_task + d_task_depot) * vehicle.energy_per_distance
    hard_reserve = vehicle.battery_capacity * config.min_battery_reserve_ratio
    end_target = vehicle.battery_capacity * config.task_end_target_ratio
    if state.battery + 1e-9 >= direct_energy_need + end_target:
        to_task = _travel_duration(d_start_task, vehicle.speed, dispatch_time, config)
        to_depot = _travel_duration(
            d_task_depot,
            vehicle.speed,
            dispatch_time + to_task + config.service_time,
            config,
        )
        completion = dispatch_time + to_task + config.service_time + to_depot
        return completion, d_start_task + d_task_depot, 0.0, state.battery - direct_energy_need, config.depot_node

    best_plan: tuple[float, float, float, float] | None = None
    # completion, distance, charging_wait, final_battery
    candidate_stations: list[tuple[int, float]] = [(station.node_id, station.charge_rate) for station in scenario.stations.values()]
    if config.allow_depot_charging:
        candidate_stations.append((config.depot_node, config.depot_charge_rate))

    for station_node, charge_rate in candidate_stations:
        d_start_station, _ = scenario.graph.shortest_path(state.current_node, station_node)
        d_station_task, _ = scenario.graph.shortest_path(station_node, task.node_id)
        d_task_depot, _ = scenario.graph.shortest_path(task.node_id, config.depot_node)
        if d_start_station == float("inf") or d_station_task == float("inf") or d_task_depot == float("inf"):
            continue

        energy_to_station = d_start_station * vehicle.energy_per_distance
        if energy_to_station > state.battery - hard_reserve + 1e-9:
            continue

        to_station = _travel_duration(d_start_station, vehicle.speed, dispatch_time, config)
        arrival = dispatch_time + to_station
        battery_at_station = state.battery - energy_to_station

        need_after_station = (d_station_task + d_task_depot) * vehicle.energy_per_distance
        if need_after_station > vehicle.battery_capacity - end_target + 1e-9:
            continue
        target = min(vehicle.battery_capacity, need_after_station + end_target)
        charge_amount = max(0.0, target - battery_at_station)
        if battery_at_station + charge_amount + 1e-9 < need_after_station + end_target:
            continue

        charge_time = charge_amount / max(1e-6, charge_rate)
        finish_charge = arrival + charge_time  # static-oracle simplification: ignore queue

        to_task = _travel_duration(d_station_task, vehicle.speed, finish_charge, config)
        to_depot = _travel_duration(
            d_task_depot,
            vehicle.speed,
            finish_charge + to_task + config.service_time,
            config,
        )

        completion = finish_charge + to_task + config.service_time + to_depot
        distance = d_start_station + d_station_task + d_task_depot
        final_battery = battery_at_station + charge_amount - need_after_station

        if best_plan is None or completion < best_plan[0]:
            best_plan = (completion, distance, 0.0, final_battery)

    if best_plan is None:
        return None

    completion, distance, wait, final_battery = best_plan
    return completion, distance, wait, final_battery, config.depot_node


def _travel_duration(distance: float, speed: float, depart: float, config: SimulationConfig) -> float:
    factor = 1.0
    for start, end, multiplier in config.rush_windows:
        if start <= depart <= end:
            factor = multiplier
            break
    return (distance / max(speed, 1e-6)) * factor


def _score_task(
    response_time: float,
    distance: float,
    charging_wait: float,
    overtime: bool,
    overtime_penalty: float,
) -> float:
    score = 260.0 - 0.95 * response_time - 0.52 * distance - 0.25 * charging_wait
    if overtime:
        score -= overtime_penalty
    return score
