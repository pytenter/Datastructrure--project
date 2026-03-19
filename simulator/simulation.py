from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple

from .graph import WeightedGraph, generate_random_city_graph
from .models import (
    ChargingStation,
    DispatchEvent,
    SimulationConfig,
    SimulationSummary,
    Task,
    TaskResult,
    Vehicle,
    VehicleMission,
)
from .strategies import DispatchStrategy


@dataclass
class ScenarioData:
    graph: WeightedGraph
    tasks: List[Task]
    vehicles: Dict[int, Vehicle]
    stations: Dict[int, ChargingStation]
    config: SimulationConfig


@dataclass
class ScenarioScale:
    name: str
    nodes: int
    extra_edges: int
    vehicles: int
    stations: int
    tasks: int
    horizon: int
    max_task_weight: float


SCENARIO_SCALES: Dict[str, ScenarioScale] = {
    "small": ScenarioScale(
        name="small",
        nodes=24,
        extra_edges=34,
        vehicles=6,
        stations=3,
        tasks=14,
        horizon=360,
        max_task_weight=18.0,
    ),
    "medium": ScenarioScale(
        name="medium",
        nodes=58,
        extra_edges=120,
        vehicles=15,
        stations=8,
        tasks=110,
        horizon=760,
        max_task_weight=19.0,
    ),
    "large": ScenarioScale(
        name="large",
        nodes=100,
        extra_edges=260,
        vehicles=28,
        stations=12,
        tasks=220,
        horizon=1250,
        max_task_weight=20.0,
    ),
}

WEATHER_MODES = ("normal", "rain", "congestion")


def build_scenario(
    scale_name: str,
    seed: int,
    allow_collaboration: bool = False,
    weather_mode: str = "normal",
) -> ScenarioData:
    if scale_name not in SCENARIO_SCALES:
        raise ValueError(f"Unknown scale: {scale_name}")
    if weather_mode not in WEATHER_MODES:
        weather_mode = "normal"

    scale = SCENARIO_SCALES[scale_name]
    rnd = random.Random(seed)

    graph = generate_random_city_graph(
        num_nodes=scale.nodes,
        extra_edges=scale.extra_edges,
        seed=seed,
        width=55.0,
        height=55.0,
        nearest_neighbors=3,
    )

    depot_node = 0
    station_nodes = _select_station_nodes(
        graph=graph,
        depot_node=depot_node,
        station_count=scale.stations,
        rnd=rnd,
    )

    stations: Dict[int, ChargingStation] = {}
    for station_id, node_id in enumerate(station_nodes):
        charge_rate = rnd.uniform(3.6, 6.4)
        ports = rnd.randint(1, 3)
        stations[station_id] = ChargingStation(
            station_id=station_id,
            node_id=node_id,
            charge_rate=charge_rate,
            ports=ports,
        )

    vehicles: Dict[int, Vehicle] = {}
    for vehicle_id in range(scale.vehicles):
        battery_capacity = rnd.uniform(130.0, 220.0)
        vehicles[vehicle_id] = Vehicle(
            vehicle_id=vehicle_id,
            capacity=rnd.uniform(9.5, 16.0),
            battery_capacity=battery_capacity,
            speed=rnd.uniform(1.5, 2.4),
            energy_per_distance=rnd.uniform(0.82, 1.15),
            current_node=depot_node,
            battery=battery_capacity * 0.90,
        )

    tasks: List[Task] = []
    caps = sorted((vehicle.capacity for vehicle in vehicles.values()), reverse=True)
    max_single_cap = caps[0]
    max_pair_cap = caps[0] + (caps[1] if len(caps) > 1 else 0.0)
    # Keep regular tasks single-vehicle serviceable even when collaboration is enabled.
    # Collaboration-heavy tasks are injected separately via collab_task_count.
    task_weight_high = min(scale.max_task_weight, max_single_cap - 0.3)
    collab_task_count = 0
    collab_weight_low = max_single_cap + 0.2
    collab_weight_high = min(scale.max_task_weight, max_pair_cap - 0.2)
    if allow_collaboration and collab_weight_high > collab_weight_low:
        collab_ratio = 0.08 if scale.name == "small" else 0.16
        collab_task_count = max(1, int(scale.tasks * collab_ratio))

    # Generate clustered task release times to create realistic dispatch peaks.
    release_bucket = {"small": 4, "medium": 6, "large": 8}[scale.name]
    jitter = max(2, int(scale.horizon * 0.03))
    peak_count = max(3, scale.tasks // 18)
    release_upper = int(scale.horizon * (0.80 if scale.name == "small" else 1.0))
    release_upper = max(release_bucket, min(scale.horizon - 1, release_upper))
    peak_centers = [rnd.randint(0, release_upper) for _ in range(peak_count)]

    for task_id in range(scale.tasks):
        if rnd.random() < 0.65:
            center = rnd.choice(peak_centers)
            release = center + rnd.randint(-jitter, jitter)
        else:
            release = rnd.randint(0, release_upper)
        release = max(0, min(scale.horizon - 1, release))
        release = (release // release_bucket) * release_bucket
        node_id = rnd.randint(1, scale.nodes - 1)
        if task_id < collab_task_count:
            weight = round(rnd.uniform(collab_weight_low, collab_weight_high), 2)
        else:
            weight = round(rnd.uniform(1.5, max(1.6, task_weight_high)), 2)
        deadline = release + rnd.randint(65, 190)
        node = graph.nodes[node_id]
        tasks.append(
            Task(
                task_id=task_id,
                release_time=release,
                node_id=node_id,
                x=node.x,
                y=node.y,
                weight=weight,
                deadline=deadline,
            )
        )

    config = SimulationConfig(
        name=scale.name,
        seed=seed,
        horizon=scale.horizon,
        depot_node=depot_node,
        service_time=3.8,
        overtime_penalty=70.0,
        unserved_penalty=60.0,
        allow_collaboration=allow_collaboration,
        min_battery_reserve_ratio=0.22 if scale.name == "small" else 0.30,
        task_end_target_ratio=0.45 if scale.name == "small" else 0.55,
        idle_recharge_trigger_ratio=0.45 if scale.name == "small" else 0.55,
        idle_recharge_target_ratio=0.90,
        allow_depot_charging=True,
        depot_charge_rate=7.2,
        depot_charge_ports=max(3, min(8, scale.vehicles // 3)),
        rush_windows=_build_weather_rush_windows(scale=scale, rnd=rnd, weather_mode=weather_mode),
        weather_mode=weather_mode,
    )

    tasks.sort(key=lambda item: (item.release_time, item.task_id))
    return ScenarioData(graph=graph, tasks=tasks, vehicles=vehicles, stations=stations, config=config)


class FleetSimulator:
    def __init__(self, scenario: ScenarioData) -> None:
        self.graph = scenario.graph
        self.tasks = scenario.tasks
        self.vehicles = scenario.vehicles
        self.stations = scenario.stations
        self.config = scenario.config
        self.depot_charger = ChargingStation(
            station_id=-1,
            node_id=self.config.depot_node,
            charge_rate=self.config.depot_charge_rate,
            ports=self.config.depot_charge_ports,
        )

    def run(self, strategy: DispatchStrategy) -> Tuple[SimulationSummary, List[TaskResult], List[DispatchEvent]]:
        pending: List[Task] = []
        completed: List[TaskResult] = []
        events: List[DispatchEvent] = []

        cursor = 0
        simulation_end = self._compute_simulation_end_time()
        last_now = 0
        for now in range(simulation_end + 1):
            last_now = now
            while cursor < len(self.tasks) and self.tasks[cursor].release_time <= now:
                pending.append(self.tasks[cursor])
                cursor += 1

            free_vehicle_ids = [
                vehicle_id
                for vehicle_id, vehicle in self.vehicles.items()
                if vehicle.available_time <= now + 1e-9
            ]
            depot_topup_candidates = [
                vehicle_id
                for vehicle_id in free_vehicle_ids
                if self.vehicles[vehicle_id].current_node == self.config.depot_node
                and self.vehicles[vehicle_id].battery
                < self.vehicles[vehicle_id].battery_capacity * self.config.idle_recharge_target_ratio - 1e-9
            ]
            if depot_topup_candidates:
                self._run_idle_recharge(depot_topup_candidates, now)
                free_vehicle_ids = [
                    vehicle_id
                    for vehicle_id, vehicle in self.vehicles.items()
                    if vehicle.available_time <= now + 1e-9
                ]

            blocked_vehicle_ids: set[int] = set()
            while pending and free_vehicle_ids:
                dispatchable_vehicle_ids = [
                    vehicle_id
                    for vehicle_id in free_vehicle_ids
                    if self.vehicles[vehicle_id].battery
                    > self.vehicles[vehicle_id].battery_capacity * self.config.min_battery_reserve_ratio + 1e-9
                ]
                if not dispatchable_vehicle_ids:
                    break

                decision = strategy.choose(
                    pending_tasks=pending,
                    free_vehicle_ids=dispatchable_vehicle_ids,
                    vehicles=self.vehicles,
                    graph=self.graph,
                    allow_collaboration=self.config.allow_collaboration,
                    now=now,
                )
                if decision is None:
                    break

                task = decision.task
                vehicle_ids = [vehicle_id for vehicle_id in decision.vehicle_ids if vehicle_id in dispatchable_vehicle_ids]
                if not vehicle_ids:
                    break

                missions: List[VehicleMission] = []
                mission_failed = False
                for vehicle_id in vehicle_ids:
                    mission = self._plan_vehicle_mission(self.vehicles[vehicle_id], task, now)
                    if mission is None:
                        mission_failed = True
                        blocked_vehicle_ids.add(vehicle_id)
                        break
                    missions.append(mission)

                if mission_failed:
                    free_vehicle_ids = [
                        vehicle_id
                        for vehicle_id in free_vehicle_ids
                        if vehicle_id not in blocked_vehicle_ids
                    ]
                    continue

                for mission in missions:
                    vehicle = self.vehicles[mission.vehicle_id]
                    vehicle.available_time = mission.completion_time
                    vehicle.current_node = mission.final_node
                    vehicle.battery = max(0.0, mission.final_battery)

                completion_time = max(mission.completion_time for mission in missions)
                travel_distance = sum(mission.travel_distance for mission in missions)
                charging_wait = sum(mission.charging_wait for mission in missions)

                result = self._score_task(
                    task=task,
                    strategy_name=strategy.name,
                    vehicle_ids=vehicle_ids,
                    completion_time=completion_time,
                    distance=travel_distance,
                    charging_wait=charging_wait,
                )
                completed.append(result)
                events.append(
                    DispatchEvent(
                        task_id=task.task_id,
                        strategy_name=strategy.name,
                        dispatch_time=float(now),
                        completion_time=completion_time,
                        vehicle_ids=vehicle_ids,
                        task_node_id=task.node_id,
                        score=result.score,
                        overtime=result.overtime,
                        total_distance=travel_distance,
                        total_charging_wait=charging_wait,
                        route_by_vehicle={mission.vehicle_id: mission.route_nodes for mission in missions},
                        station_by_vehicle={mission.vehicle_id: mission.charged_station_id for mission in missions},
                        travel_distance_by_vehicle={mission.vehicle_id: mission.travel_distance for mission in missions},
                        completion_time_by_vehicle={mission.vehicle_id: mission.completion_time for mission in missions},
                        final_battery_by_vehicle={mission.vehicle_id: mission.final_battery for mission in missions},
                        start_battery_by_vehicle={mission.vehicle_id: mission.start_battery for mission in missions},
                        final_node_by_vehicle={mission.vehicle_id: mission.final_node for mission in missions},
                        charge_amount_by_vehicle={mission.vehicle_id: mission.charge_amount for mission in missions},
                        charging_wait_by_vehicle={mission.vehicle_id: mission.charging_wait for mission in missions},
                        charge_start_time_by_vehicle={mission.vehicle_id: mission.charge_start_time for mission in missions},
                        charge_end_time_by_vehicle={mission.vehicle_id: mission.charge_end_time for mission in missions},
                    )
                )

                pending = [item for item in pending if item.task_id != task.task_id]
                free_vehicle_ids = [
                    vehicle_id
                    for vehicle_id in free_vehicle_ids
                    if self.vehicles[vehicle_id].available_time <= now + 1e-9
                ]

            idle_vehicle_ids = [
                vehicle_id
                for vehicle_id, vehicle in self.vehicles.items()
                if vehicle.available_time <= now + 1e-9
            ]
            if idle_vehicle_ids:
                self._run_idle_recharge(idle_vehicle_ids, now)

            # Stop early once all tasks are released/cleared and all vehicles are idle.
            if cursor >= len(self.tasks) and not pending:
                all_idle = all(vehicle.available_time <= now + 1e-9 for vehicle in self.vehicles.values())
                if all_idle:
                    break

        unserved_tasks = pending + self.tasks[cursor:]
        total_score = sum(item.score for item in completed) - self.config.unserved_penalty * len(unserved_tasks)
        avg_response_time = (
            sum(item.response_time for item in completed) / len(completed)
            if completed
            else 0.0
        )

        summary = SimulationSummary(
            scenario=self.config.name,
            strategy=strategy.name,
            total_tasks=len(self.tasks),
            completed_tasks=len(completed),
            unserved_tasks=len(unserved_tasks),
            overtime_tasks=sum(1 for item in completed if item.overtime),
            total_distance=sum(item.distance for item in completed),
            avg_response_time=avg_response_time,
            total_charging_wait=sum(item.charging_wait for item in completed),
            final_score=total_score,
            station_utilization={
                station_id: station.utilization(last_now)
                for station_id, station in self.stations.items()
            },
        )
        return summary, completed, events

    def _compute_simulation_end_time(self) -> int:
        if not self.tasks:
            return int(self.config.horizon)
        max_release = max(task.release_time for task in self.tasks)
        max_deadline = max(task.deadline for task in self.tasks)
        # Keep a post-deadline tail window so late released tasks still have service opportunities.
        tail_window = max(40, int(0.2 * self.config.horizon))
        return int(max(self.config.horizon, max_release, max_deadline) + tail_window)

    def _plan_vehicle_mission(self, vehicle: Vehicle, task: Task, now: float) -> VehicleMission | None:
        depot = self.config.depot_node

        d_start_task, path_start_task = self.graph.shortest_path(vehicle.current_node, task.node_id)
        d_task_depot, path_task_depot = self.graph.shortest_path(task.node_id, depot)
        if d_start_task == float("inf") or d_task_depot == float("inf"):
            return None

        direct_need = (d_start_task + d_task_depot) * vehicle.energy_per_distance
        hard_reserve_energy = vehicle.battery_capacity * self.config.min_battery_reserve_ratio
        end_target_energy = vehicle.battery_capacity * self.config.task_end_target_ratio
        if vehicle.battery <= hard_reserve_energy + 1e-9:
            return None
        if vehicle.battery + 1e-9 >= direct_need + end_target_energy:
            to_task_time = self._travel_duration(d_start_task, vehicle.speed, now)
            to_depot_time = self._travel_duration(d_task_depot, vehicle.speed, now + to_task_time + self.config.service_time)
            completion = now + to_task_time + self.config.service_time + to_depot_time
            route_nodes = _merge_paths(path_start_task, path_task_depot)
            return VehicleMission(
                vehicle_id=vehicle.vehicle_id,
                start_time=now,
                completion_time=completion,
                start_battery=vehicle.battery,
                charge_start_time=None,
                charge_end_time=None,
                travel_distance=d_start_task + d_task_depot,
                final_battery=vehicle.battery - direct_need,
                final_node=depot,
                charging_wait=0.0,
                charge_amount=0.0,
                charged_station_id=None,
                route_nodes=route_nodes,
            )

        station_plan = self._pick_charging_plan(vehicle=vehicle, task=task, now=now)
        if station_plan is None:
            return None

        (
            station,
            d_start_station,
            d_station_task,
            d_task_depot,
            path_start_station,
            path_station_task,
            path_task_depot,
            arrival,
            charge_amount,
        ) = station_plan

        charge_duration = charge_amount / station.charge_rate if charge_amount > 0 else 0.0
        start_charge, finish_charge = station.reserve(arrival_time=arrival, charge_duration=charge_duration)

        energy_to_station = d_start_station * vehicle.energy_per_distance
        energy_after_station = (d_station_task + d_task_depot) * vehicle.energy_per_distance
        battery_after_arrival = vehicle.battery - energy_to_station
        final_battery = battery_after_arrival + charge_amount - energy_after_station

        to_task_time = self._travel_duration(d_station_task, vehicle.speed, finish_charge)
        to_depot_time = self._travel_duration(
            d_task_depot,
            vehicle.speed,
            finish_charge + to_task_time + self.config.service_time,
        )

        completion = finish_charge + to_task_time + self.config.service_time + to_depot_time
        route_nodes = _merge_paths(path_start_station, path_station_task, path_task_depot)
        return VehicleMission(
            vehicle_id=vehicle.vehicle_id,
            start_time=now,
            completion_time=completion,
            start_battery=vehicle.battery,
            charge_start_time=start_charge,
            charge_end_time=finish_charge,
            travel_distance=d_start_station + d_station_task + d_task_depot,
            final_battery=final_battery,
            final_node=depot,
            charging_wait=start_charge - arrival,
            charge_amount=charge_amount,
            charged_station_id=station.station_id,
            route_nodes=route_nodes,
        )

    def _pick_charging_plan(
        self,
        vehicle: Vehicle,
        task: Task,
        now: float,
    ) -> tuple[ChargingStation, float, float, float, List[int], List[int], List[int], float, float] | None:
        depot = self.config.depot_node
        best_score = float("inf")
        best_plan: tuple[ChargingStation, float, float, float, List[int], List[int], List[int], float, float] | None = None

        for station in self._iter_candidate_stations():
            d_start_station, path_start_station = self.graph.shortest_path(vehicle.current_node, station.node_id)
            d_station_task, path_station_task = self.graph.shortest_path(station.node_id, task.node_id)
            d_task_depot, path_task_depot = self.graph.shortest_path(task.node_id, depot)
            if (
                d_start_station == float("inf")
                or d_station_task == float("inf")
                or d_task_depot == float("inf")
            ):
                continue

            hard_reserve_energy = vehicle.battery_capacity * self.config.min_battery_reserve_ratio
            end_target_energy = vehicle.battery_capacity * self.config.task_end_target_ratio
            energy_to_station = d_start_station * vehicle.energy_per_distance
            if energy_to_station > vehicle.battery - hard_reserve_energy + 1e-9:
                continue

            to_station_time = self._travel_duration(d_start_station, vehicle.speed, now)
            arrival = now + to_station_time
            battery_after_arrival = vehicle.battery - energy_to_station

            need_after_station = (d_station_task + d_task_depot) * vehicle.energy_per_distance
            if need_after_station > vehicle.battery_capacity - end_target_energy + 1e-9:
                continue
            target_energy = min(vehicle.battery_capacity, need_after_station + end_target_energy)
            charge_amount = max(0.0, target_energy - battery_after_arrival)

            if battery_after_arrival + charge_amount + 1e-9 < need_after_station + end_target_energy:
                continue

            charge_duration = charge_amount / station.charge_rate if charge_amount > 0 else 0.0
            start_charge = station.expected_start_time(arrival)
            finish_charge = start_charge + charge_duration

            to_task_time = self._travel_duration(d_station_task, vehicle.speed, finish_charge)
            to_depot_time = self._travel_duration(
                d_task_depot,
                vehicle.speed,
                finish_charge + to_task_time + self.config.service_time,
            )

            mission_finish = finish_charge + to_task_time + self.config.service_time + to_depot_time
            wait = station.expected_wait_time(arrival)
            load_penalty = 16.0 * station.utilization(now)
            score = mission_finish + 0.55 * wait + load_penalty

            if score < best_score:
                best_score = score
                best_plan = (
                    station,
                    d_start_station,
                    d_station_task,
                    d_task_depot,
                    path_start_station,
                    path_station_task,
                    path_task_depot,
                    arrival,
                    charge_amount,
                )

        return best_plan

    def _iter_candidate_stations(self) -> List[ChargingStation]:
        stations = list(self.stations.values())
        if self.config.allow_depot_charging:
            stations.append(self.depot_charger)
        return stations

    def _run_idle_recharge(self, free_vehicle_ids: Sequence[int], now: float) -> List[int]:
        for vehicle_id in free_vehicle_ids:
            vehicle = self.vehicles[vehicle_id]
            mission = self._plan_idle_recharge(vehicle, now)
            if mission is None:
                continue
            vehicle.available_time = mission.completion_time
            vehicle.current_node = mission.final_node
            vehicle.battery = max(0.0, mission.final_battery)

        return [
            vehicle_id
            for vehicle_id in free_vehicle_ids
            if self.vehicles[vehicle_id].available_time <= now + 1e-9
        ]

    def _plan_idle_recharge(self, vehicle: Vehicle, now: float) -> VehicleMission | None:
        if vehicle.battery_capacity <= 1e-9:
            return None

        depot = self.config.depot_node
        battery_ratio = vehicle.battery / vehicle.battery_capacity
        at_depot = vehicle.current_node == depot
        if at_depot:
            if battery_ratio + 1e-9 >= self.config.idle_recharge_target_ratio:
                return None
        elif battery_ratio + 1e-9 >= self.config.idle_recharge_trigger_ratio:
            return None

        reserve_energy = vehicle.battery_capacity * self.config.min_battery_reserve_ratio
        target_energy_abs = vehicle.battery_capacity * self.config.idle_recharge_target_ratio

        best_score = float("inf")
        best_plan: tuple[
            ChargingStation,
            float,
            float,
            List[int],
            List[int],
            float,
            float,
            float,
            float,
            float,
            float,
        ] | None = None

        candidates = [self.depot_charger] if at_depot and self.config.allow_depot_charging else self._iter_candidate_stations()
        for station in candidates:
            d_start_station, path_start_station = self.graph.shortest_path(vehicle.current_node, station.node_id)
            d_station_depot, path_station_depot = self.graph.shortest_path(station.node_id, depot)
            if d_start_station == float("inf") or d_station_depot == float("inf"):
                continue

            energy_to_station = d_start_station * vehicle.energy_per_distance
            if energy_to_station > vehicle.battery - reserve_energy + 1e-9:
                continue

            to_station_time = self._travel_duration(d_start_station, vehicle.speed, now)
            arrival = now + to_station_time
            battery_after_arrival = vehicle.battery - energy_to_station

            need_after_station = d_station_depot * vehicle.energy_per_distance + reserve_energy
            if need_after_station > vehicle.battery_capacity + 1e-9:
                continue

            target_energy = min(vehicle.battery_capacity, max(target_energy_abs, need_after_station))
            charge_amount = max(0.0, target_energy - battery_after_arrival)
            if charge_amount <= 1e-9:
                continue

            charge_duration = charge_amount / station.charge_rate if charge_amount > 0 else 0.0
            start_charge = station.expected_start_time(arrival)
            finish_charge = start_charge + charge_duration
            charging_wait = start_charge - arrival

            to_depot_time = self._travel_duration(d_station_depot, vehicle.speed, finish_charge)
            completion = finish_charge + to_depot_time
            final_battery = battery_after_arrival + charge_amount - d_station_depot * vehicle.energy_per_distance
            if final_battery + 1e-9 < reserve_energy:
                continue

            load_penalty = 12.0 * station.utilization(now)
            score = completion + 0.55 * charging_wait + load_penalty

            if score < best_score:
                best_score = score
                best_plan = (
                    station,
                    d_start_station,
                    d_station_depot,
                    path_start_station,
                    path_station_depot,
                    charge_amount,
                    charging_wait,
                    arrival,
                    start_charge,
                    finish_charge,
                    final_battery,
                )

        if best_plan is None:
            return None

        (
            station,
            d_start_station,
            d_station_depot,
            path_start_station,
            path_station_depot,
            charge_amount,
            charging_wait,
            arrival,
            start_charge,
            finish_charge,
            final_battery,
        ) = best_plan
        charge_duration = charge_amount / station.charge_rate if charge_amount > 0 else 0.0
        actual_charge_start, actual_charge_end = station.reserve(arrival_time=arrival, charge_duration=charge_duration)
        charging_wait = actual_charge_start - arrival
        finish_charge = actual_charge_end
        to_depot_time = self._travel_duration(d_station_depot, vehicle.speed, finish_charge)
        completion_time = finish_charge + to_depot_time
        route_nodes = _merge_paths(path_start_station, path_station_depot)

        return VehicleMission(
            vehicle_id=vehicle.vehicle_id,
            start_time=now,
            completion_time=completion_time,
            start_battery=vehicle.battery,
            charge_start_time=actual_charge_start,
            charge_end_time=finish_charge,
            travel_distance=d_start_station + d_station_depot,
            final_battery=final_battery,
            final_node=depot,
            charging_wait=charging_wait,
            charge_amount=charge_amount,
            charged_station_id=station.station_id,
            route_nodes=route_nodes,
        )

    def _score_task(
        self,
        task: Task,
        strategy_name: str,
        vehicle_ids: Sequence[int],
        completion_time: float,
        distance: float,
        charging_wait: float,
    ) -> TaskResult:
        response_time = completion_time - task.release_time
        overtime = completion_time > task.deadline

        score = 260.0 - 0.95 * response_time - 0.52 * distance - 0.25 * charging_wait
        if overtime:
            score -= self.config.overtime_penalty

        return TaskResult(
            task_id=task.task_id,
            strategy_name=strategy_name,
            vehicle_ids=list(vehicle_ids),
            release_time=task.release_time,
            completion_time=completion_time,
            distance=distance,
            response_time=response_time,
            overtime=overtime,
            score=score,
            charging_wait=charging_wait,
        )

    def _traffic_multiplier(self, when: float) -> float:
        for start, end, multiplier in self.config.rush_windows:
            if start <= when <= end:
                return multiplier
        return 1.0

    def _travel_duration(self, distance: float, speed: float, depart_time: float) -> float:
        base = distance / max(speed, 1e-6)
        return base * self._traffic_multiplier(depart_time)


def _merge_paths(*paths: List[int]) -> List[int]:
    merged: List[int] = []
    for path in paths:
        if not path:
            continue
        if not merged:
            merged.extend(path)
            continue
        merged.extend(path[1:])
    return merged


def _build_weather_rush_windows(
    scale: ScenarioScale,
    rnd: random.Random,
    weather_mode: str,
) -> List[tuple[int, int, float]]:
    base = [
        (int(scale.horizon * 0.18), int(scale.horizon * 0.34), 1.45),
        (int(scale.horizon * 0.56), int(scale.horizon * 0.72), 1.30),
    ]
    if weather_mode == "normal":
        return base

    if weather_mode == "rain":
        factor = 1.17
        incident_count = 2
        incident_mul = (1.22, 1.40)
    else:
        # congestion
        factor = 1.35
        incident_count = 3
        incident_mul = (1.35, 1.72)

    windows: List[tuple[int, int, float]] = [
        (start, end, mult * factor) for start, end, mult in base
    ]
    for _ in range(incident_count):
        center = rnd.randint(int(0.08 * scale.horizon), int(0.92 * scale.horizon))
        half = rnd.randint(max(10, int(0.035 * scale.horizon)), max(16, int(0.08 * scale.horizon)))
        start = max(0, center - half)
        end = min(scale.horizon, center + half)
        mul = rnd.uniform(*incident_mul)
        windows.append((start, end, mul))
    windows.sort(key=lambda item: (item[0], item[1]))
    return windows


def _select_station_nodes(
    graph: WeightedGraph,
    depot_node: int,
    station_count: int,
    rnd: random.Random,
) -> List[int]:
    candidates = [node_id for node_id in graph.nodes if node_id != depot_node]
    if station_count >= len(candidates):
        return sorted(candidates)

    depot = graph.nodes[depot_node]
    dist_to_depot = {
        node_id: math.hypot(graph.nodes[node_id].x - depot.x, graph.nodes[node_id].y - depot.y)
        for node_id in candidates
    }

    first = max(candidates, key=lambda node_id: (dist_to_depot[node_id], rnd.random()))
    selected = [first]
    selected_set = {first}

    while len(selected) < station_count:
        best_node = None
        best_score = -1.0
        for node_id in candidates:
            if node_id in selected_set:
                continue
            min_dist_to_selected = min(
                math.hypot(
                    graph.nodes[node_id].x - graph.nodes[other_id].x,
                    graph.nodes[node_id].y - graph.nodes[other_id].y,
                )
                for other_id in selected
            )
            score = min_dist_to_selected + 0.35 * dist_to_depot[node_id] + rnd.random() * 1e-6
            if score > best_score:
                best_score = score
                best_node = node_id
        if best_node is None:
            break
        selected.append(best_node)
        selected_set.add(best_node)

    return sorted(selected)


def run_strategies_for_scenario(
    scenario: ScenarioData,
    strategies: Iterable[DispatchStrategy],
) -> List[tuple[SimulationSummary, List[TaskResult], List[DispatchEvent]]]:
    outputs: List[tuple[SimulationSummary, List[TaskResult], List[DispatchEvent]]] = []
    for strategy in strategies:
        sim = FleetSimulator(copy.deepcopy(scenario))
        outputs.append(sim.run(strategy))
    return outputs
