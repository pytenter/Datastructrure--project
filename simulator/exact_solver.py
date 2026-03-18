from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .models import SimulationConfig, Task, Vehicle
from .simulation import ScenarioData

try:
    import gurobipy as gp
    from gurobipy import GRB

    HAS_GUROBI = True
except Exception:
    HAS_GUROBI = False

try:
    from docplex.mp.model import Model as CplexModel
    import cplex  # noqa: F401

    HAS_CPLEX = True
except Exception:
    HAS_CPLEX = False


@dataclass
class ExactSolveResult:
    backend: str
    status: str
    optimal: bool
    mip_gap: float | None
    runtime_sec: float
    objective_value: float
    completed: int
    unserved: int
    overtime: int
    total_distance: float
    avg_response_time: float
    total_charging_wait: float
    final_score: float
    assignments: Dict[int, int]


def solve_with_gurobi(
    scenario: ScenarioData,
    time_limit_sec: int = 120,
    mip_gap: float = 0.0,
) -> ExactSolveResult:
    if not HAS_GUROBI:
        raise RuntimeError("gurobipy is not available in current environment")

    start = time.time()
    allow_collaboration = bool(scenario.config.allow_collaboration)
    data = _build_exact_data(scenario, allow_collaboration=allow_collaboration)

    tasks = data["tasks"]
    vehicles = data["vehicles"]
    capacity = data["capacity"]
    release = data["release"]
    deadline = data["deadline"]
    feasible_k_by_i = data["feasible_k_by_i"]
    proc = data["proc"]
    dist = data["dist"]
    wait = data["wait"]

    task_count = len(tasks)
    vehicle_count = len(vehicles)

    model = gp.Model("ev_dispatch_exact")
    model.Params.OutputFlag = 0
    model.Params.TimeLimit = float(time_limit_sec)
    model.Params.MIPGap = float(mip_gap)

    x_keys = [(i, k) for i in range(task_count) for k in feasible_k_by_i[i]]
    x = model.addVars(x_keys, vtype=GRB.BINARY, name="x")
    y = model.addVars(range(task_count), vtype=GRB.BINARY, name="y")
    late = model.addVars(range(task_count), vtype=GRB.BINARY, name="late")
    s = model.addVars(range(task_count), lb=0.0, vtype=GRB.CONTINUOUS, name="s")
    c = model.addVars(range(task_count), lb=0.0, vtype=GRB.CONTINUOUS, name="c")

    max_proc = max((proc[(i, k)] for (i, k) in x_keys), default=1.0)
    big_m = max(1.0, scenario.config.horizon + task_count * max_proc)

    for i in range(task_count):
        if feasible_k_by_i[i]:
            if allow_collaboration:
                model.addConstr(gp.quicksum(x[i, k] for k in feasible_k_by_i[i]) >= y[i], name=f"assign_cover_{i}")
                model.addConstr(
                    gp.quicksum(capacity[k] * x[i, k] for k in feasible_k_by_i[i]) >= tasks[i].weight * y[i],
                    name=f"cap_cover_{i}",
                )
                for k in feasible_k_by_i[i]:
                    model.addConstr(x[i, k] <= y[i], name=f"assign_link_{i}_{k}")
            else:
                model.addConstr(gp.quicksum(x[i, k] for k in feasible_k_by_i[i]) == y[i], name=f"assign_{i}")
        else:
            model.addConstr(y[i] == 0, name=f"assign_none_{i}")

        model.addConstr(s[i] >= release[i] * y[i], name=f"release_{i}")
        model.addConstr(s[i] <= big_m * y[i], name=f"start_bound_{i}")
        model.addConstr(c[i] <= big_m * y[i], name=f"comp_bound_{i}")
        model.addConstr(late[i] <= y[i], name=f"late_bound_{i}")

        for k in feasible_k_by_i[i]:
            model.addConstr(c[i] >= s[i] + proc[i, k] - big_m * (1 - x[i, k]), name=f"duration_lb_{i}_{k}")
        model.addConstr(c[i] - deadline[i] <= big_m * late[i], name=f"deadline_{i}")

    order_keys: List[Tuple[int, int, int]] = []
    for k in range(vehicle_count):
        task_ids = [i for i in range(task_count) if k in feasible_k_by_i[i]]
        for idx_i in range(len(task_ids)):
            for idx_j in range(idx_i + 1, len(task_ids)):
                i = task_ids[idx_i]
                j = task_ids[idx_j]
                order_keys.append((i, j, k))

    u = model.addVars(order_keys, vtype=GRB.BINARY, name="u")

    for i, j, k in order_keys:
        model.addConstr(
            s[j] >= s[i] + proc[i, k] - big_m * (1 - u[i, j, k]) - big_m * (2 - x[i, k] - x[j, k]),
            name=f"seq_ij_{i}_{j}_{k}",
        )
        model.addConstr(
            s[i] >= s[j] + proc[j, k] - big_m * u[i, j, k] - big_m * (2 - x[i, k] - x[j, k]),
            name=f"seq_ji_{i}_{j}_{k}",
        )

    objective = gp.quicksum((320.0 + 0.95 * release[i]) * y[i] - 0.95 * c[i] - scenario.config.overtime_penalty * late[i] for i in range(task_count))
    objective -= gp.quicksum((0.52 * dist[i, k] + 0.25 * wait[i, k]) * x[i, k] for (i, k) in x_keys)
    model.setObjective(objective, GRB.MAXIMIZE)

    model.optimize()

    if model.SolCount == 0:
        raise RuntimeError("Gurobi failed to produce a feasible solution")

    status = _status_name(model.Status)
    optimal = model.Status == GRB.OPTIMAL
    mip_gap_out = None
    if model.Status in {GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.INTERRUPTED, GRB.SUBOPTIMAL}:
        mip_gap_out = float(model.MIPGap)

    assignments: Dict[int, int] = {}
    served_scores: List[float] = []
    distances: List[float] = []
    response_times: List[float] = []
    waits: List[float] = []
    overtime_count = 0

    for i in range(task_count):
        if y[i].X < 0.5:
            continue

        chosen_ks: List[int] = []
        for k in feasible_k_by_i[i]:
            if x[i, k].X > 0.5:
                chosen_ks.append(k)
        if not chosen_ks:
            continue

        assignments[tasks[i].task_id] = vehicles[chosen_ks[0]].vehicle_id
        completion = c[i].X
        response = completion - release[i]
        is_overtime = completion > deadline[i] + 1e-9
        if is_overtime:
            overtime_count += 1

        task_distance = sum(dist[i, k] for k in chosen_ks)
        task_wait = sum(wait[i, k] for k in chosen_ks)
        score = 260.0 - 0.95 * response - 0.52 * task_distance - 0.25 * task_wait
        if is_overtime:
            score -= scenario.config.overtime_penalty

        served_scores.append(score)
        distances.append(task_distance)
        response_times.append(response)
        waits.append(task_wait)

    completed = len(assignments)
    unserved = task_count - completed
    final_score = sum(served_scores) - scenario.config.unserved_penalty * unserved

    return ExactSolveResult(
        backend="gurobi",
        status=status,
        optimal=optimal,
        mip_gap=mip_gap_out,
        runtime_sec=time.time() - start,
        objective_value=float(model.ObjVal),
        completed=completed,
        unserved=unserved,
        overtime=overtime_count,
        total_distance=sum(distances),
        avg_response_time=(sum(response_times) / completed if completed else 0.0),
        total_charging_wait=sum(waits),
        final_score=final_score,
        assignments=assignments,
    )


def solve_with_cplex(
    scenario: ScenarioData,
    time_limit_sec: int = 120,
    mip_gap: float = 0.0,
) -> ExactSolveResult:
    if not HAS_CPLEX:
        raise RuntimeError("docplex/cplex is not available in current environment")

    start = time.time()
    allow_collaboration = bool(scenario.config.allow_collaboration)
    data = _build_exact_data(scenario, allow_collaboration=allow_collaboration)

    tasks = data["tasks"]
    vehicles = data["vehicles"]
    capacity = data["capacity"]
    release = data["release"]
    deadline = data["deadline"]
    feasible_k_by_i = data["feasible_k_by_i"]
    proc = data["proc"]
    dist = data["dist"]
    wait = data["wait"]

    task_count = len(tasks)
    vehicle_count = len(vehicles)

    mdl = CplexModel(name="ev_dispatch_exact_cplex")
    mdl.context.solver.log_output = False
    mdl.parameters.timelimit = float(time_limit_sec)
    mdl.parameters.mip.tolerances.mipgap = float(mip_gap)
    # Large instances benefit from finding incumbents early.
    mdl.parameters.emphasis.mip = 1

    x_keys = [(i, k) for i in range(task_count) for k in feasible_k_by_i[i]]
    x = mdl.binary_var_dict(x_keys, name="x")
    y = mdl.binary_var_list(task_count, name="y")
    late = mdl.binary_var_list(task_count, name="late")
    s = mdl.continuous_var_list(task_count, lb=0.0, name="s")
    c = mdl.continuous_var_list(task_count, lb=0.0, name="c")

    max_proc = max((proc[(i, k)] for (i, k) in x_keys), default=1.0)
    big_m = max(1.0, scenario.config.horizon + task_count * max_proc)

    for i in range(task_count):
        if feasible_k_by_i[i]:
            if allow_collaboration:
                mdl.add_constraint(mdl.sum(x[i, k] for k in feasible_k_by_i[i]) >= y[i], ctname=f"assign_cover_{i}")
                mdl.add_constraint(
                    mdl.sum(capacity[k] * x[i, k] for k in feasible_k_by_i[i]) >= tasks[i].weight * y[i],
                    ctname=f"cap_cover_{i}",
                )
                for k in feasible_k_by_i[i]:
                    mdl.add_constraint(x[i, k] <= y[i], ctname=f"assign_link_{i}_{k}")
            else:
                mdl.add_constraint(mdl.sum(x[i, k] for k in feasible_k_by_i[i]) == y[i], ctname=f"assign_{i}")
        else:
            mdl.add_constraint(y[i] == 0, ctname=f"assign_none_{i}")

        mdl.add_constraint(s[i] >= release[i] * y[i], ctname=f"release_{i}")
        mdl.add_constraint(s[i] <= big_m * y[i], ctname=f"start_bound_{i}")
        mdl.add_constraint(c[i] <= big_m * y[i], ctname=f"comp_bound_{i}")
        mdl.add_constraint(late[i] <= y[i], ctname=f"late_bound_{i}")

        for k in feasible_k_by_i[i]:
            mdl.add_constraint(c[i] >= s[i] + proc[i, k] - big_m * (1 - x[i, k]), ctname=f"duration_lb_{i}_{k}")
        mdl.add_constraint(c[i] - deadline[i] <= big_m * late[i], ctname=f"deadline_{i}")

    order_keys: List[Tuple[int, int, int]] = []
    for k in range(vehicle_count):
        task_ids = [i for i in range(task_count) if k in feasible_k_by_i[i]]
        for idx_i in range(len(task_ids)):
            for idx_j in range(idx_i + 1, len(task_ids)):
                i = task_ids[idx_i]
                j = task_ids[idx_j]
                order_keys.append((i, j, k))

    u = mdl.binary_var_dict(order_keys, name="u")
    for i, j, k in order_keys:
        mdl.add_constraint(
            s[j] >= s[i] + proc[i, k] - big_m * (1 - u[i, j, k]) - big_m * (2 - x[i, k] - x[j, k]),
            ctname=f"seq_ij_{i}_{j}_{k}",
        )
        mdl.add_constraint(
            s[i] >= s[j] + proc[j, k] - big_m * u[i, j, k] - big_m * (2 - x[i, k] - x[j, k]),
            ctname=f"seq_ji_{i}_{j}_{k}",
        )

    obj = mdl.sum((320.0 + 0.95 * release[i]) * y[i] - 0.95 * c[i] - scenario.config.overtime_penalty * late[i] for i in range(task_count))
    obj -= mdl.sum((0.52 * dist[i, k] + 0.25 * wait[i, k]) * x[i, k] for (i, k) in x_keys)
    mdl.maximize(obj)

    _add_cplex_warm_start(
        mdl=mdl,
        scenario=scenario,
        release=release,
        deadline=deadline,
        task_weight=[float(task.weight) for task in tasks],
        capacity=capacity,
        allow_collaboration=allow_collaboration,
        feasible_k_by_i=feasible_k_by_i,
        proc=proc,
        dist=dist,
        wait=wait,
        x=x,
        y=y,
        late=late,
        s=s,
        c=c,
        x_keys=x_keys,
    )

    sol = mdl.solve(log_output=False)
    if sol is None:
        raise RuntimeError("CPLEX failed to produce a feasible solution")

    solve_details = mdl.solve_details
    status = str(solve_details.status or "UNKNOWN")
    optimal = str(status).upper().find("OPTIMAL") >= 0
    mip_gap_out = solve_details.mip_relative_gap

    assignments: Dict[int, int] = {}
    served_scores: List[float] = []
    distances: List[float] = []
    response_times: List[float] = []
    waits: List[float] = []
    overtime_count = 0

    for i in range(task_count):
        if y[i].solution_value < 0.5:
            continue

        chosen_ks: List[int] = []
        for k in feasible_k_by_i[i]:
            if x[i, k].solution_value > 0.5:
                chosen_ks.append(k)
        if not chosen_ks:
            continue

        assignments[tasks[i].task_id] = vehicles[chosen_ks[0]].vehicle_id
        completion = c[i].solution_value
        response = completion - release[i]
        is_overtime = completion > deadline[i] + 1e-9
        if is_overtime:
            overtime_count += 1

        task_distance = sum(dist[i, k] for k in chosen_ks)
        task_wait = sum(wait[i, k] for k in chosen_ks)
        score = 260.0 - 0.95 * response - 0.52 * task_distance - 0.25 * task_wait
        if is_overtime:
            score -= scenario.config.overtime_penalty

        served_scores.append(score)
        distances.append(task_distance)
        response_times.append(response)
        waits.append(task_wait)

    completed = len(assignments)
    unserved = task_count - completed
    final_score = sum(served_scores) - scenario.config.unserved_penalty * unserved

    return ExactSolveResult(
        backend="cplex",
        status=status,
        optimal=optimal,
        mip_gap=(float(mip_gap_out) if mip_gap_out is not None else None),
        runtime_sec=time.time() - start,
        objective_value=float(sol.objective_value),
        completed=completed,
        unserved=unserved,
        overtime=overtime_count,
        total_distance=sum(distances),
        avg_response_time=(sum(response_times) / completed if completed else 0.0),
        total_charging_wait=sum(waits),
        final_score=final_score,
        assignments=assignments,
    )


def _add_cplex_warm_start(
    mdl: CplexModel,
    scenario: ScenarioData,
    release: List[float],
    deadline: List[float],
    task_weight: List[float],
    capacity: List[float],
    allow_collaboration: bool,
    feasible_k_by_i: Dict[int, List[int]],
    proc: Dict[Tuple[int, int], float],
    dist: Dict[Tuple[int, int], float],
    wait: Dict[Tuple[int, int], float],
    x,
    y,
    late,
    s,
    c,
    x_keys: List[Tuple[int, int]],
) -> None:
    task_count = len(release)
    if task_count == 0:
        return

    vehicle_indices = sorted({k for _, k in x_keys})
    if not vehicle_indices:
        return

    available_time = {k: 0.0 for k in vehicle_indices}
    assigned_ks_by_i: Dict[int, List[int]] = {}
    start_by_i: Dict[int, float] = {}
    comp_by_i: Dict[int, float] = {}
    late_by_i: Dict[int, int] = {}

    task_order = sorted(range(task_count), key=lambda i: (release[i], deadline[i], i))
    for i in task_order:
        candidates = feasible_k_by_i.get(i, [])
        if not candidates:
            continue

        if allow_collaboration:
            ordered = sorted(
                candidates,
                key=lambda k: (
                    available_time.get(k, 0.0) + proc[i, k],
                    (0.52 * dist[i, k] + 0.25 * wait[i, k]),
                    -capacity[k],
                ),
            )
            chosen_ks: List[int] = []
            cap_sum = 0.0
            for k in ordered:
                chosen_ks.append(k)
                cap_sum += capacity[k]
                if cap_sum + 1e-9 >= task_weight[i]:
                    break
            if cap_sum + 1e-9 < task_weight[i]:
                continue
        else:
            best_k = max(
                candidates,
                key=lambda k: (
                    (320.0 + 0.95 * release[i])
                    - (0.95 * (max(release[i], available_time.get(k, 0.0)) + proc[i, k]))
                    - (0.52 * dist[i, k] + 0.25 * wait[i, k]),
                    capacity[k],
                ),
            )
            chosen_ks = [best_k]

        start_time = max(release[i], max(available_time.get(k, 0.0) for k in chosen_ks))
        completion_by_k = {k: start_time + proc[i, k] for k in chosen_ks}
        task_completion = max(completion_by_k.values())
        is_late = 1 if task_completion > deadline[i] + 1e-9 else 0
        contrib = (
            (320.0 + 0.95 * release[i])
            - 0.95 * task_completion
            - scenario.config.overtime_penalty * is_late
            - sum((0.52 * dist[i, k] + 0.25 * wait[i, k]) for k in chosen_ks)
        )
        if contrib <= 1e-9:
            continue

        assigned_ks_by_i[i] = chosen_ks
        start_by_i[i] = start_time
        comp_by_i[i] = task_completion
        late_by_i[i] = is_late
        for k in chosen_ks:
            available_time[k] = completion_by_k[k]

    if not assigned_ks_by_i:
        return

    warm = mdl.new_solution()
    for i in range(task_count):
        assigned = i in assigned_ks_by_i
        warm.add_var_value(y[i], 1 if assigned else 0)
        warm.add_var_value(s[i], start_by_i.get(i, 0.0))
        warm.add_var_value(c[i], comp_by_i.get(i, 0.0))
        warm.add_var_value(late[i], late_by_i.get(i, 0))

    for i, k in x_keys:
        warm.add_var_value(x[i, k], 1 if k in assigned_ks_by_i.get(i, []) else 0)

    mdl.add_mip_start(warm)


def _build_exact_data(scenario: ScenarioData, allow_collaboration: bool = False) -> dict:
    tasks = list(scenario.tasks)
    vehicles = list(scenario.vehicles.values())
    capacity = [float(vehicle.capacity) for vehicle in vehicles]

    release = [float(task.release_time) for task in tasks]
    deadline = [float(task.deadline) for task in tasks]

    feasible_k_by_i: Dict[int, List[int]] = {i: [] for i in range(len(tasks))}
    proc: Dict[Tuple[int, int], float] = {}
    dist: Dict[Tuple[int, int], float] = {}
    wait: Dict[Tuple[int, int], float] = {}

    for i, task in enumerate(tasks):
        for k, vehicle in enumerate(vehicles):
            if not allow_collaboration and task.weight > vehicle.capacity + 1e-9:
                continue
            mission = _mission_from_depot(task, vehicle, scenario)
            if mission is None:
                continue

            ptime, dsum, wait_time = mission
            feasible_k_by_i[i].append(k)
            proc[i, k] = ptime
            dist[i, k] = dsum
            wait[i, k] = wait_time

    return {
        "tasks": tasks,
        "vehicles": vehicles,
        "capacity": capacity,
        "release": release,
        "deadline": deadline,
        "feasible_k_by_i": feasible_k_by_i,
        "proc": proc,
        "dist": dist,
        "wait": wait,
    }


def _mission_from_depot(task: Task, vehicle: Vehicle, scenario: ScenarioData) -> tuple[float, float, float] | None:
    depot = scenario.config.depot_node

    d_start_task, _ = scenario.graph.shortest_path(depot, task.node_id)
    d_task_depot, _ = scenario.graph.shortest_path(task.node_id, depot)
    if math.isinf(d_start_task) or math.isinf(d_task_depot):
        return None

    direct_need = (d_start_task + d_task_depot) * vehicle.energy_per_distance
    battery_at_start = vehicle.battery_capacity
    hard_reserve = vehicle.battery_capacity * scenario.config.min_battery_reserve_ratio
    end_target = vehicle.battery_capacity * scenario.config.task_end_target_ratio
    if battery_at_start + 1e-9 >= direct_need + end_target:
        duration = d_start_task / max(vehicle.speed, 1e-6) + scenario.config.service_time + d_task_depot / max(vehicle.speed, 1e-6)
        return duration, d_start_task + d_task_depot, 0.0

    best: tuple[float, float] | None = None
    candidate_stations: list[tuple[int, float]] = [(station.node_id, station.charge_rate) for station in scenario.stations.values()]
    if scenario.config.allow_depot_charging:
        candidate_stations.append((depot, scenario.config.depot_charge_rate))

    for station_node, charge_rate in candidate_stations:
        d_start_station, _ = scenario.graph.shortest_path(depot, station_node)
        d_station_task, _ = scenario.graph.shortest_path(station_node, task.node_id)
        d_task_depot, _ = scenario.graph.shortest_path(task.node_id, depot)
        if math.isinf(d_start_station) or math.isinf(d_station_task) or math.isinf(d_task_depot):
            continue

        energy_to_station = d_start_station * vehicle.energy_per_distance
        if energy_to_station > battery_at_start - hard_reserve + 1e-9:
            continue

        battery_after_arrival = battery_at_start - energy_to_station
        need_after_station = (d_station_task + d_task_depot) * vehicle.energy_per_distance
        if need_after_station > vehicle.battery_capacity - end_target + 1e-9:
            continue
        target = min(vehicle.battery_capacity, need_after_station + end_target)
        charge_amount = max(0.0, target - battery_after_arrival)

        if battery_after_arrival + charge_amount + 1e-9 < need_after_station + end_target:
            continue

        charge_time = charge_amount / max(1e-6, charge_rate)
        duration = (
            d_start_station / max(vehicle.speed, 1e-6)
            + charge_time
            + d_station_task / max(vehicle.speed, 1e-6)
            + scenario.config.service_time
            + d_task_depot / max(vehicle.speed, 1e-6)
        )
        dsum = d_start_station + d_station_task + d_task_depot

        if best is None or duration < best[0]:
            best = (duration, dsum)

    if best is None:
        return None

    return best[0], best[1], 0.0


def _status_name(code: int) -> str:
    if not HAS_GUROBI:
        return str(code)
    mapping = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.SUBOPTIMAL: "SUBOPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.INTERRUPTED: "INTERRUPTED",
    }
    return mapping.get(code, f"STATUS_{code}")
