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
    data = _build_exact_data(scenario)

    tasks = data["tasks"]
    vehicles = data["vehicles"]
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
            model.addConstr(gp.quicksum(x[i, k] for k in feasible_k_by_i[i]) == y[i], name=f"assign_{i}")
        else:
            model.addConstr(y[i] == 0, name=f"assign_none_{i}")

        model.addConstr(s[i] >= release[i] * y[i], name=f"release_{i}")
        model.addConstr(s[i] <= big_m * y[i], name=f"start_bound_{i}")
        model.addConstr(c[i] <= big_m * y[i], name=f"comp_bound_{i}")
        model.addConstr(late[i] <= y[i], name=f"late_bound_{i}")

        proc_expr = gp.quicksum(proc[i, k] * x[i, k] for k in feasible_k_by_i[i])
        model.addConstr(c[i] == s[i] + proc_expr, name=f"duration_{i}")
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
            s[j] >= c[i] - big_m * (1 - u[i, j, k]) - big_m * (2 - x[i, k] - x[j, k]),
            name=f"seq_ij_{i}_{j}_{k}",
        )
        model.addConstr(
            s[i] >= c[j] - big_m * u[i, j, k] - big_m * (2 - x[i, k] - x[j, k]),
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

        chosen_k = None
        for k in feasible_k_by_i[i]:
            if x[i, k].X > 0.5:
                chosen_k = k
                break
        if chosen_k is None:
            continue

        assignments[tasks[i].task_id] = vehicles[chosen_k].vehicle_id
        completion = c[i].X
        response = completion - release[i]
        is_overtime = completion > deadline[i] + 1e-9
        if is_overtime:
            overtime_count += 1

        task_distance = dist[i, chosen_k]
        task_wait = wait[i, chosen_k]
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
    data = _build_exact_data(scenario)

    tasks = data["tasks"]
    vehicles = data["vehicles"]
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
            mdl.add_constraint(mdl.sum(x[i, k] for k in feasible_k_by_i[i]) == y[i], ctname=f"assign_{i}")
        else:
            mdl.add_constraint(y[i] == 0, ctname=f"assign_none_{i}")

        mdl.add_constraint(s[i] >= release[i] * y[i], ctname=f"release_{i}")
        mdl.add_constraint(s[i] <= big_m * y[i], ctname=f"start_bound_{i}")
        mdl.add_constraint(c[i] <= big_m * y[i], ctname=f"comp_bound_{i}")
        mdl.add_constraint(late[i] <= y[i], ctname=f"late_bound_{i}")

        proc_expr = mdl.sum(proc[i, k] * x[i, k] for k in feasible_k_by_i[i])
        mdl.add_constraint(c[i] == s[i] + proc_expr, ctname=f"duration_{i}")
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
            s[j] >= c[i] - big_m * (1 - u[i, j, k]) - big_m * (2 - x[i, k] - x[j, k]),
            ctname=f"seq_ij_{i}_{j}_{k}",
        )
        mdl.add_constraint(
            s[i] >= c[j] - big_m * u[i, j, k] - big_m * (2 - x[i, k] - x[j, k]),
            ctname=f"seq_ji_{i}_{j}_{k}",
        )

    obj = mdl.sum((320.0 + 0.95 * release[i]) * y[i] - 0.95 * c[i] - scenario.config.overtime_penalty * late[i] for i in range(task_count))
    obj -= mdl.sum((0.52 * dist[i, k] + 0.25 * wait[i, k]) * x[i, k] for (i, k) in x_keys)
    mdl.maximize(obj)

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

        chosen_k = None
        for k in feasible_k_by_i[i]:
            if x[i, k].solution_value > 0.5:
                chosen_k = k
                break
        if chosen_k is None:
            continue

        assignments[tasks[i].task_id] = vehicles[chosen_k].vehicle_id
        completion = c[i].solution_value
        response = completion - release[i]
        is_overtime = completion > deadline[i] + 1e-9
        if is_overtime:
            overtime_count += 1

        task_distance = dist[i, chosen_k]
        task_wait = wait[i, chosen_k]
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


def _build_exact_data(scenario: ScenarioData) -> dict:
    tasks = list(scenario.tasks)
    vehicles = list(scenario.vehicles.values())

    release = [float(task.release_time) for task in tasks]
    deadline = [float(task.deadline) for task in tasks]

    feasible_k_by_i: Dict[int, List[int]] = {i: [] for i in range(len(tasks))}
    proc: Dict[Tuple[int, int], float] = {}
    dist: Dict[Tuple[int, int], float] = {}
    wait: Dict[Tuple[int, int], float] = {}

    for i, task in enumerate(tasks):
        for k, vehicle in enumerate(vehicles):
            if task.weight > vehicle.capacity + 1e-9:
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
