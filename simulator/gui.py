from __future__ import annotations

import copy
import json
import webbrowser
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, TypeVar
from urllib.parse import urlparse

from .exact_solver import HAS_CPLEX, solve_with_cplex
from .simulation import SCENARIO_SCALES, WEATHER_MODES, FleetSimulator, ScenarioData, build_scenario, run_strategies_for_scenario
from .strategies import (
    AuctionBasedStrategy,
    HyperHeuristicStrategy,
    MaxWeightFirstStrategy,
    NearestTaskFirstStrategy,
    ReinforcementLearningDispatchStrategy,
    SimulatedAnnealingStrategy,
    UrgencyDistanceStrategy,
)


STRATEGY_REGISTRY = {
    "nearest_task_first": NearestTaskFirstStrategy,
    "max_task_first": MaxWeightFirstStrategy,
    "urgency_distance": UrgencyDistanceStrategy,
    "auction_multi_agent": AuctionBasedStrategy,
    "metaheuristic_sa": SimulatedAnnealingStrategy,
    "reinforcement_q": ReinforcementLearningDispatchStrategy,
    "hyper_heuristic_ucb": HyperHeuristicStrategy,
}

WEB_DIR = Path(__file__).with_name("web")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = PROJECT_ROOT / "results"

BENCHMARK_PRESETS: List[Tuple[str, str, str]] = [
    ("cplex", "Dynamic vs Static (CPLEX)", "summary_cplex.json"),
    ("dynamic_only", "Dynamic Only", "summary_dynamic.json"),
]

BENCHMARK_METRICS = [
    {"id": "score", "label": "Score", "direction": "desc"},
    {"id": "completed", "label": "Completed", "direction": "desc"},
    {"id": "unserved", "label": "Unserved", "direction": "asc"},
    {"id": "overtime", "label": "Overtime", "direction": "asc"},
    {"id": "distance", "label": "Distance", "direction": "asc"},
    {"id": "avg_response_time", "label": "Avg Response Time", "direction": "asc"},
    {"id": "charging_wait", "label": "Charging Wait", "direction": "asc"},
]

T = TypeVar("T")
PROMO_MODEL_VAR_LIMIT = 1000
PROMO_MODEL_CONSTR_LIMIT = 1000
PROMO_SAFETY_MARGIN = 0.90


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/meta":
            self._write_json(
                {
                    "scales": list(SCENARIO_SCALES.keys()),
                    "strategies": list(STRATEGY_REGISTRY.keys()),
                    "defaults": {
                        "scale": "small",
                        "strategy": "urgency_distance",
                        "seed": 20260309,
                        "allow_collaboration": True,
                        "weather_mode": "normal",
                    },
                    "weather_modes": list(WEATHER_MODES),
                }
            )
            return
        if path == "/api/benchmarks":
            self._write_json(_load_benchmark_payload())
            return
        if path == "/api/weather-stats":
            self._write_json(_load_weather_stats_payload())
            return

        if path in {"/", "/index.html"}:
            self._write_file("index.html", "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self._write_file("styles.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._write_file("app.js", "application/javascript; charset=utf-8")
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/api/run":
            payload = self._read_json_body()
            response = _run_single_simulation(payload)
            self._write_json(response)
            return

        if parsed.path == "/api/compare":
            payload = self._read_json_body()
            response = _compare_strategies(payload)
            self._write_json(response)
            return
        if parsed.path == "/api/weather-stats":
            payload = self._read_json_body()
            response = _weather_stats(payload)
            self._write_json(response)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            return {}
        raw = self.rfile.read(content_length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _write_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_file(self, filename: str, content_type: str) -> None:
        file_path = WEB_DIR / filename
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _run_single_simulation(payload: dict) -> dict:
    scale, seed, allow_collaboration, weather_mode = _extract_common_args(payload)
    scenario_seed = _scenario_seed_for_scale(seed, scale)
    strategy_name = str(payload.get("strategy", "urgency_distance"))
    strategy = _build_strategy_instance(strategy_name, scenario_seed)

    scenario = build_scenario(
        scale_name=scale,
        seed=scenario_seed,
        allow_collaboration=allow_collaboration,
        weather_mode=weather_mode,
    )
    scenario_snapshot = copy.deepcopy(scenario)
    simulator = FleetSimulator(copy.deepcopy(scenario))
    summary, _, events = simulator.run(strategy)

    task_states = {task.task_id: "pending" for task in scenario.tasks}
    task_by_id = {task.task_id: task for task in scenario.tasks}
    normalized_events = sorted(events, key=lambda e: (e.dispatch_time, e.completion_time, e.task_id))

    event_payload = []
    for event in normalized_events:
        route_items = []
        task = task_by_id[event.task_id]
        assigned_weight = task.weight / max(1, len(event.vehicle_ids))
        for vehicle_id, route_nodes in event.route_by_vehicle.items():
            route_items.append(
                {
                    "vehicle_id": int(vehicle_id),
                    "task_id": int(event.task_id),
                    "route_nodes": list(route_nodes),
                    "station_id": event.station_by_vehicle.get(vehicle_id),
                    "travel_distance": float(event.travel_distance_by_vehicle.get(vehicle_id, 0.0)),
                    "completion_time": float(event.completion_time_by_vehicle.get(vehicle_id, event.completion_time)),
                    "start_battery": float(event.start_battery_by_vehicle.get(vehicle_id, 0.0)),
                    "final_battery": float(event.final_battery_by_vehicle.get(vehicle_id, 0.0)),
                    "final_node": int(event.final_node_by_vehicle.get(vehicle_id, scenario.config.depot_node)),
                    "charge_amount": float(event.charge_amount_by_vehicle.get(vehicle_id, 0.0)),
                    "charging_wait": float(event.charging_wait_by_vehicle.get(vehicle_id, 0.0)),
                    "charge_start_time": event.charge_start_time_by_vehicle.get(vehicle_id),
                    "charge_end_time": event.charge_end_time_by_vehicle.get(vehicle_id),
                    "assigned_weight": float(assigned_weight),
                }
            )
        event_payload.append(
            {
                "task_id": event.task_id,
                "strategy_name": event.strategy_name,
                "dispatch_time": event.dispatch_time,
                "completion_time": event.completion_time,
                "vehicle_ids": list(event.vehicle_ids),
                "task_node_id": event.task_node_id,
                "score": event.score,
                "overtime": event.overtime,
                "total_distance": event.total_distance,
                "total_charging_wait": event.total_charging_wait,
                "routes": route_items,
            }
        )

    return {
        "scenario": _serialize_scenario(scenario_snapshot),
        "summary": asdict(summary),
        "task_state": task_states,
        "events": event_payload,
    }


def _compare_strategies(payload: dict) -> dict:
    scale, seed, allow_collaboration, weather_mode = _extract_common_args(payload)
    base_seed = _scenario_seed_for_scale(seed, scale)
    runs = payload.get("compare_runs", 3)
    try:
        runs = int(runs)
    except (TypeError, ValueError):
        runs = 3
    runs = min(5, max(1, runs))

    aggr: Dict[str, dict] = {}
    strategy_names = [
        "nearest_task_first",
        "max_task_first",
        "urgency_distance",
        "auction_multi_agent",
        "metaheuristic_sa",
        "reinforcement_q",
        "hyper_heuristic_ucb",
    ]
    for name in strategy_names:
        aggr[name] = {
            "strategy": name,
            "score_sum": 0.0,
            "completed_sum": 0.0,
            "overtime_sum": 0.0,
            "unserved_sum": 0.0,
            "scores": [],
        }

    for idx in range(runs):
        scenario_seed = base_seed + idx * 17
        scenario = build_scenario(
            scale_name=scale,
            seed=scenario_seed,
            allow_collaboration=allow_collaboration,
            weather_mode=weather_mode,
        )
        strategy_instances = [
            NearestTaskFirstStrategy(),
            MaxWeightFirstStrategy(),
            UrgencyDistanceStrategy(),
            AuctionBasedStrategy(),
            SimulatedAnnealingStrategy(seed=scenario_seed),
            ReinforcementLearningDispatchStrategy(seed=scenario_seed + 1),
            HyperHeuristicStrategy(seed=scenario_seed + 2),
        ]
        outputs = run_strategies_for_scenario(scenario, strategy_instances)
        for summary, _, _ in outputs:
            bucket = aggr[summary.strategy]
            bucket["score_sum"] += summary.final_score
            bucket["completed_sum"] += summary.completed_tasks
            bucket["overtime_sum"] += summary.overtime_tasks
            bucket["unserved_sum"] += summary.unserved_tasks
            bucket["scores"].append(summary.final_score)

    ranking = []
    for name in strategy_names:
        item = aggr[name]
        score_avg = item["score_sum"] / runs
        completed_avg = item["completed_sum"] / runs
        overtime_avg = item["overtime_sum"] / runs
        unserved_avg = item["unserved_sum"] / runs
        ranking.append(
            {
                "strategy": name,
                "score": score_avg,
                "completed": int(round(completed_avg)),
                "overtime": int(round(overtime_avg)),
                "unserved": int(round(unserved_avg)),
            }
        )

    ranking.sort(key=lambda item: item["score"], reverse=True)
    return {"ranking": ranking, "compare_runs": runs}


def _extract_common_args(payload: dict) -> Tuple[str, int, bool, str]:
    scale = str(payload.get("scale", "small"))
    if scale not in SCENARIO_SCALES:
        scale = "small"

    try:
        seed = int(payload.get("seed", 20260309))
    except (TypeError, ValueError):
        seed = 20260309

    allow_collaboration = bool(payload.get("allow_collaboration", True))
    weather_mode = str(payload.get("weather_mode", "normal"))
    if weather_mode not in WEATHER_MODES:
        weather_mode = "normal"
    return scale, seed, allow_collaboration, weather_mode


def _weather_stats(payload: dict) -> dict:
    _, seed, allow_collaboration, _ = _extract_common_args(payload)
    include_static_cplex = bool(payload.get("include_static_cplex", True))
    exact_time_limit = max(10, _safe_int(payload.get("exact_time_limit", 120), 120))
    exact_mip_gap = max(0.0, _safe_float(payload.get("exact_mip_gap", 0.0), 0.0))
    strategy_names = list(STRATEGY_REGISTRY.keys())
    rows: List[dict] = []
    normal_static_by_scale = (
        _load_normal_static_cplex_rows(seed=seed, allow_collaboration=allow_collaboration)
        if include_static_cplex
        else {}
    )

    for scale in SCENARIO_SCALES.keys():
        # Align with benchmark seed policy in main.py: seed + idx * 100.
        # Within the same scale, weather modes share the same scenario seed so
        # comparison only reflects weather/traffic differences.
        scenario_seed = _scenario_seed_for_scale(seed, scale)
        for weather_mode in WEATHER_MODES:
            scenario = build_scenario(
                scale_name=scale,
                seed=scenario_seed,
                allow_collaboration=allow_collaboration,
                weather_mode=weather_mode,
            )
            strategy_instances = []
            for i, name in enumerate(strategy_names):
                # Keep seed policy consistent with main.py benchmark runner.
                strategy_instances.append(_build_strategy_instance(name, scenario_seed + i))
            outputs = run_strategies_for_scenario(scenario, strategy_instances)
            for summary, _, _ in outputs:
                rows.append(
                    {
                        "scenario": summary.scenario,
                        "weather": weather_mode,
                        "strategy": summary.strategy,
                        "mode": "dynamic",
                        "completed": summary.completed_tasks,
                        "overtime": summary.overtime_tasks,
                        "unserved": summary.unserved_tasks,
                        "score": summary.final_score,
                        "distance": summary.total_distance,
                        "avg_response_time": summary.avg_response_time,
                        "charging_wait": summary.total_charging_wait,
                        "seed": scenario_seed,
                    }
                )
            if include_static_cplex:
                if weather_mode == "normal" and scale in normal_static_by_scale:
                    rows.append(dict(normal_static_by_scale[scale]))
                else:
                    rows.append(
                        _solve_weather_static_cplex(
                            scale=scale,
                            seed=scenario_seed,
                            allow_collaboration=allow_collaboration,
                            weather_mode=weather_mode,
                            exact_time_limit=exact_time_limit,
                            exact_mip_gap=exact_mip_gap,
                        )
                    )

    payload_out = {
        "rows": rows,
        "scales": list(SCENARIO_SCALES.keys()),
        "weather_modes": list(WEATHER_MODES),
        "strategies": strategy_names + (["static_exact_fullinfo"] if include_static_cplex else []),
        "include_static_cplex": include_static_cplex,
        "exact_time_limit": exact_time_limit,
        "exact_mip_gap": exact_mip_gap,
        "saved_file": "",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = RESULTS_DIR / "weather_stats.json"
        # Persist rows in benchmark-like format (list of row dicts).
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        payload_out["saved_file"] = out_path.name
    except Exception:
        payload_out["saved_file"] = ""
    return payload_out


def _build_strategy_instance(strategy_name: str, seed: int):
    cls = STRATEGY_REGISTRY.get(strategy_name, UrgencyDistanceStrategy)
    if cls in {SimulatedAnnealingStrategy, ReinforcementLearningDispatchStrategy, HyperHeuristicStrategy}:
        return cls(seed=seed)
    return cls()


def _scenario_seed_for_scale(base_seed: int, scale: str) -> int:
    scales = list(SCENARIO_SCALES.keys())
    try:
        idx = scales.index(scale)
    except ValueError:
        idx = 0
    return int(base_seed) + idx * 100


def _load_weather_stats_payload() -> dict:
    path = RESULTS_DIR / "weather_stats.json"
    if not path.exists() or not path.is_file():
        return {
            "rows": [],
            "scales": list(SCENARIO_SCALES.keys()),
            "weather_modes": list(WEATHER_MODES),
            "strategies": [],
            "saved_file": "",
            "updated_at": "",
            "error": "weather_stats.json not found",
        }

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "rows": [],
            "scales": list(SCENARIO_SCALES.keys()),
            "weather_modes": list(WEATHER_MODES),
            "strategies": [],
            "saved_file": path.name,
            "updated_at": _format_mtime(path),
            "error": "Invalid JSON content",
        }

    rows_raw = raw.get("rows", []) if isinstance(raw, dict) else raw
    rows: List[dict] = []
    if isinstance(rows_raw, list):
        for item in rows_raw:
            if isinstance(item, dict):
                rows.append(item)

    scales = sorted({str(row.get("scenario", "")) for row in rows if row.get("scenario")})
    weather_modes = sorted({str(row.get("weather", "")) for row in rows if row.get("weather")})
    strategies = sorted({str(row.get("strategy", "")) for row in rows if row.get("strategy")})

    return {
        "rows": rows,
        "scales": scales or list(SCENARIO_SCALES.keys()),
        "weather_modes": weather_modes or list(WEATHER_MODES),
        "strategies": strategies,
        "saved_file": path.name,
        "updated_at": _format_mtime(path),
    }


def _load_normal_static_cplex_rows(seed: int, allow_collaboration: bool) -> Dict[str, dict]:
    path = RESULTS_DIR / "summary_cplex.json"
    if not path.exists() or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, list):
        return {}

    result: Dict[str, dict] = {}
    for scale in SCENARIO_SCALES.keys():
        scenario_seed = _scenario_seed_for_scale(seed, scale)
        picked = _pick_best_static_cplex_row(
            raw_rows=raw,
            scale=scale,
            scenario_seed=scenario_seed,
            allow_collaboration=allow_collaboration,
        )
        if picked is None:
            continue
        result[scale] = _normalize_static_weather_row(
            row=picked,
            scale=scale,
            weather_mode="normal",
            fallback_seed=scenario_seed,
            source="summary_cplex.json",
        )
    return result


def _pick_best_static_cplex_row(
    raw_rows: List[object],
    scale: str,
    scenario_seed: int,
    allow_collaboration: bool,
) -> dict | None:
    best_row = None
    best_score = -10**9
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        if str(item.get("scenario", "")) != scale:
            continue
        mode = str(item.get("mode", ""))
        if not mode.startswith("static_exact_cplex"):
            continue
        score = 0
        row_seed = _safe_int(item.get("seed", -1), -1)
        if row_seed == scenario_seed:
            score += 120
        elif row_seed >= 0:
            score += 20

        if "allow_collaboration" in item and bool(item.get("allow_collaboration")) == allow_collaboration:
            score += 30

        if "failed" not in mode.lower():
            score += 40
        if "reduced" not in mode.lower():
            score += 8

        solver_status = str(item.get("solver_status", ""))
        if solver_status.upper().startswith("FAILED"):
            score -= 25

        if score > best_score:
            best_score = score
            best_row = item
    return best_row


def _normalize_static_weather_row(
    row: dict,
    scale: str,
    weather_mode: str,
    fallback_seed: int,
    source: str,
) -> dict:
    return {
        "scenario": scale,
        "weather": weather_mode,
        "strategy": str(row.get("strategy", "static_exact_fullinfo")),
        "mode": str(row.get("mode", "static_exact_cplex")),
        "completed": _safe_int(row.get("completed", 0), 0),
        "overtime": _safe_int(row.get("overtime", 0), 0),
        "unserved": _safe_int(row.get("unserved", 0), 0),
        "score": _safe_float(row.get("score", 0.0), 0.0),
        "distance": _safe_float(row.get("distance", 0.0), 0.0),
        "avg_response_time": _safe_float(row.get("avg_response_time", 0.0), 0.0),
        "charging_wait": _safe_float(row.get("charging_wait", 0.0), 0.0),
        "seed": _safe_int(row.get("seed", fallback_seed), fallback_seed),
        "solver_backend": row.get("solver_backend", "cplex"),
        "solver_status": row.get("solver_status"),
        "solver_optimal": row.get("solver_optimal"),
        "source": source,
    }


def _solve_weather_static_cplex(
    scale: str,
    seed: int,
    allow_collaboration: bool,
    weather_mode: str,
    exact_time_limit: int,
    exact_mip_gap: float,
) -> dict:
    scenario = build_scenario(
        scale_name=scale,
        seed=seed,
        allow_collaboration=allow_collaboration,
        weather_mode=weather_mode,
    )
    if not HAS_CPLEX:
        return _build_static_cplex_failed_weather_row(
            scenario=scenario,
            weather_mode=weather_mode,
            mode="static_exact_cplex_failed",
            strategy="static_exact_fullinfo",
            solver_status="FAILED: docplex/cplex is not available in current environment",
        )

    exact_scenario = scenario
    exact_meta = _build_weather_exact_meta(scenario)
    exact = None
    fail_exc: Exception | None = None
    try:
        exact = solve_with_cplex(
            exact_scenario,
            time_limit_sec=exact_time_limit,
            mip_gap=exact_mip_gap,
        )
    except Exception as exc:
        fail_exc = exc
        should_retry_reduced = (
            scale in {"medium", "large"}
            and _is_cplex_license_limit_error(str(exc))
        )
        if should_retry_reduced:
            retry_scenario, retry_meta = _prepare_weather_exact_scenario_for_license(
                scenario=scenario,
                scale_name=scale,
            )
            if retry_meta["exact_reduced_for_license"]:
                exact_scenario = retry_scenario
                exact_meta = retry_meta
                try:
                    exact = solve_with_cplex(
                        exact_scenario,
                        time_limit_sec=exact_time_limit,
                        mip_gap=exact_mip_gap,
                    )
                    fail_exc = None
                except Exception as retry_exc:
                    fail_exc = retry_exc

    if exact is not None and fail_exc is None:
        mode_name = "static_exact_cplex_reduced" if exact_meta["exact_reduced_for_license"] else "static_exact_cplex"
        strategy_name = (
            "static_exact_fullinfo_reduced"
            if exact_meta["exact_reduced_for_license"]
            else "static_exact_fullinfo"
        )
        return {
            "scenario": scale,
            "weather": weather_mode,
            "strategy": strategy_name,
            "mode": mode_name,
            "completed": int(exact.completed),
            "unserved": int(exact.unserved),
            "overtime": int(exact.overtime),
            "distance": float(exact.total_distance),
            "avg_response_time": float(exact.avg_response_time),
            "charging_wait": float(exact.total_charging_wait),
            "score": float(exact.final_score),
            "seed": int(seed),
            "solver_backend": exact.backend,
            "solver_status": exact.status,
            "solver_optimal": exact.optimal,
            "solver_gap": exact.mip_gap,
            "solver_runtime_sec": exact.runtime_sec,
            "solver_objective": exact.objective_value,
            "source": "computed",
            **exact_meta,
        }

    fail_mode = (
        "static_exact_cplex_reduced_failed"
        if exact_meta["exact_reduced_for_license"]
        else "static_exact_cplex_failed"
    )
    fail_strategy = (
        "static_exact_fullinfo_reduced"
        if exact_meta["exact_reduced_for_license"]
        else "static_exact_fullinfo"
    )
    return _build_static_cplex_failed_weather_row(
        scenario=exact_scenario,
        weather_mode=weather_mode,
        mode=fail_mode,
        strategy=fail_strategy,
        solver_status=f"FAILED: {fail_exc}",
        **exact_meta,
    )


def _build_static_cplex_failed_weather_row(
    scenario: ScenarioData,
    weather_mode: str,
    mode: str,
    strategy: str,
    solver_status: str,
    **extra: object,
) -> dict:
    task_count = len(scenario.tasks)
    return {
        "scenario": scenario.config.name,
        "weather": weather_mode,
        "strategy": strategy,
        "mode": mode,
        "completed": 0,
        "unserved": task_count,
        "overtime": 0,
        "distance": 0.0,
        "avg_response_time": 0.0,
        "charging_wait": 0.0,
        "score": -scenario.config.unserved_penalty * task_count,
        "seed": int(scenario.config.seed),
        "solver_backend": "cplex",
        "solver_status": solver_status,
        "source": "computed",
        **extra,
    }


def _build_weather_exact_meta(scenario: ScenarioData) -> dict:
    return {
        "exact_reduced_for_license": False,
        "exact_original_tasks": len(scenario.tasks),
        "exact_original_vehicles": len(scenario.vehicles),
        "exact_reduction_factor": 1.0,
        "exact_task_count": len(scenario.tasks),
        "exact_vehicle_count": len(scenario.vehicles),
    }


def _estimate_weather_exact_model_upper_bound(task_count: int, vehicle_count: int) -> tuple[int, int]:
    x_vars = task_count * vehicle_count
    base_vars = 4 * task_count
    order_vars = vehicle_count * task_count * (task_count - 1) // 2
    var_count = x_vars + base_vars + order_vars
    base_constraints = 7 * task_count
    order_constraints = 2 * order_vars
    constraint_count = base_constraints + order_constraints
    return var_count, constraint_count


def _find_weather_license_safe_reduction(task_count: int, vehicle_count: int) -> tuple[int, int, float]:
    var_limit = int(PROMO_MODEL_VAR_LIMIT * PROMO_SAFETY_MARGIN)
    constr_limit = int(PROMO_MODEL_CONSTR_LIMIT * PROMO_SAFETY_MARGIN)
    min_task = 2 if task_count >= 2 else 1
    min_vehicle = 2 if vehicle_count >= 2 else 1

    factor = 1.0
    while factor >= 0.03:
        reduced_tasks = min(task_count, max(min_task, int(round(task_count * factor))))
        reduced_vehicles = min(vehicle_count, max(min_vehicle, int(round(vehicle_count * factor))))
        vars_est, constr_est = _estimate_weather_exact_model_upper_bound(reduced_tasks, reduced_vehicles)
        if vars_est <= var_limit and constr_est <= constr_limit:
            return reduced_tasks, reduced_vehicles, factor
        factor *= 0.94

    return min(task_count, 12), min(vehicle_count, max(min_vehicle, 2)), factor


def _sample_evenly(items: List[T], target_count: int) -> List[T]:
    if target_count >= len(items):
        return list(items)
    if target_count <= 0:
        return []
    if target_count == 1:
        return [items[0]]

    n = len(items)
    indices = [int(i * n / target_count) for i in range(target_count)]
    indices[-1] = n - 1

    unique_indices: List[int] = []
    seen = set()
    for idx in indices:
        if idx in seen:
            continue
        seen.add(idx)
        unique_indices.append(idx)

    if len(unique_indices) < target_count:
        for idx in range(n):
            if idx in seen:
                continue
            unique_indices.append(idx)
            seen.add(idx)
            if len(unique_indices) >= target_count:
                break

    unique_indices.sort()
    return [items[idx] for idx in unique_indices[:target_count]]


def _build_weather_reduced_exact_scenario(scenario: ScenarioData, task_count: int, vehicle_count: int) -> ScenarioData:
    tasks_sorted = sorted(scenario.tasks, key=lambda item: (item.release_time, item.deadline, item.task_id))
    reduced_tasks = _sample_evenly(tasks_sorted, task_count)

    vehicles_sorted = sorted(
        scenario.vehicles.values(),
        key=lambda item: (-item.capacity, item.vehicle_id),
    )
    selected_vehicle_ids = [vehicle.vehicle_id for vehicle in vehicles_sorted[:vehicle_count]]
    reduced_vehicles = {vehicle_id: scenario.vehicles[vehicle_id] for vehicle_id in sorted(selected_vehicle_ids)}

    return ScenarioData(
        graph=scenario.graph,
        tasks=reduced_tasks,
        vehicles=reduced_vehicles,
        stations=scenario.stations,
        config=scenario.config,
    )


def _prepare_weather_exact_scenario_for_license(
    scenario: ScenarioData,
    scale_name: str,
) -> tuple[ScenarioData, dict]:
    meta = _build_weather_exact_meta(scenario)
    if scale_name not in {"medium", "large"}:
        return scenario, meta

    reduced_tasks, reduced_vehicles, factor = _find_weather_license_safe_reduction(
        task_count=len(scenario.tasks),
        vehicle_count=len(scenario.vehicles),
    )
    if reduced_tasks >= len(scenario.tasks) and reduced_vehicles >= len(scenario.vehicles):
        return scenario, meta

    reduced_scenario = _build_weather_reduced_exact_scenario(
        scenario=scenario,
        task_count=reduced_tasks,
        vehicle_count=reduced_vehicles,
    )
    meta.update(
        {
            "exact_reduced_for_license": True,
            "exact_reduction_factor": round(factor, 4),
            "exact_task_count": reduced_tasks,
            "exact_vehicle_count": reduced_vehicles,
        }
    )
    return reduced_scenario, meta


def _is_cplex_license_limit_error(text: str) -> bool:
    lowered = str(text).lower()
    keywords = [
        "size limit",
        "problem size",
        "too many variables",
        "too many constraints",
        "community edition",
        "promotional",
        "license",
        "cplex error 1016",
        "error 1016",
    ]
    return any(keyword in lowered for keyword in keywords)


def _serialize_scenario(scenario) -> dict:
    edges = _unique_edges(scenario.graph._adj.items())  # pylint: disable=protected-access
    return {
        "nodes": [
            {
                "node_id": node.node_id,
                "x": node.x,
                "y": node.y,
            }
            for node in scenario.graph.nodes.values()
        ],
        "edges": [{"a": a, "b": b} for a, b in edges],
        "tasks": [
            {
                "task_id": task.task_id,
                "node_id": task.node_id,
                "release_time": task.release_time,
                "deadline": task.deadline,
                "weight": task.weight,
            }
            for task in scenario.tasks
        ],
        "vehicles": [
            {
                "vehicle_id": vehicle.vehicle_id,
                "capacity": vehicle.capacity,
                "battery_capacity": vehicle.battery_capacity,
                "battery": vehicle.battery,
                "speed": vehicle.speed,
                "energy_per_distance": vehicle.energy_per_distance,
                "current_node": vehicle.current_node,
            }
            for vehicle in scenario.vehicles.values()
        ],
        "stations": [
            {
                "station_id": station.station_id,
                "node_id": station.node_id,
                "ports": station.ports,
                "charge_rate": station.charge_rate,
            }
            for station in scenario.stations.values()
        ],
        "depot_node": scenario.config.depot_node,
        "weather_mode": scenario.config.weather_mode,
    }


def _unique_edges(adj_items: Iterable[Tuple[int, List[Tuple[int, float]]]]) -> List[Tuple[int, int]]:
    seen = set()
    edges: List[Tuple[int, int]] = []
    for src, neighbors in adj_items:
        for dst, _ in neighbors:
            key = tuple(sorted((src, dst)))
            if key in seen:
                continue
            seen.add(key)
            edges.append(key)
    return edges


def _load_benchmark_payload() -> dict:
    datasets: List[dict] = []
    seen_names: set[str] = set()

    for key, label, filename in BENCHMARK_PRESETS:
        dataset = _read_benchmark_dataset(key=key, label=label, path=RESULTS_DIR / filename)
        if dataset is not None:
            datasets.append(dataset)
            seen_names.add(filename.lower())

    latest_path = RESULTS_DIR / "summary.json"
    if not datasets and latest_path.exists() and latest_path.name.lower() not in seen_names:
        latest = _read_benchmark_dataset(key="latest", label="Latest Summary", path=latest_path)
        if latest is not None:
            datasets.append(latest)

    default_key = None
    preferred_keys = ["cplex", "dynamic_only", "latest"]
    for pref in preferred_keys:
        if any(item["key"] == pref for item in datasets):
            default_key = pref
            break
    if default_key is None and datasets:
        default_key = datasets[0]["key"]
    return {
        "datasets": datasets,
        "metrics": BENCHMARK_METRICS,
        "defaults": {
            "dataset_key": default_key,
            "scenario": "all",
            "metric": "score",
        },
    }


def _read_benchmark_dataset(key: str, label: str, path: Path) -> dict | None:
    if not path.exists() or not path.is_file():
        return None

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "key": key,
            "label": label,
            "filename": path.name,
            "updated_at": _format_mtime(path),
            "row_count": 0,
            "rows": [],
            "error": "Invalid JSON content",
        }

    rows: List[dict] = []
    if isinstance(raw, list):
        for row in raw:
            normalized = _normalize_benchmark_row(row)
            if normalized is not None:
                rows.append(normalized)

    return {
        "key": key,
        "label": label,
        "filename": path.name,
        "updated_at": _format_mtime(path),
        "row_count": len(rows),
        "rows": rows,
    }


def _normalize_benchmark_row(row: object) -> dict | None:
    if not isinstance(row, dict):
        return None
    mode = str(row.get("mode", "-"))
    solver_backend = row.get("solver_backend")
    solver_backend_name = str(solver_backend).lower() if solver_backend is not None else ""
    if "gurobi" in mode.lower() or solver_backend_name == "gurobi":
        return None
    return {
        "scenario": str(row.get("scenario", "-")),
        "strategy": str(row.get("strategy", "-")),
        "mode": mode,
        "completed": _safe_int(row.get("completed", 0)),
        "unserved": _safe_int(row.get("unserved", 0)),
        "overtime": _safe_int(row.get("overtime", 0)),
        "distance": _safe_float(row.get("distance", 0.0)),
        "avg_response_time": _safe_float(row.get("avg_response_time", 0.0)),
        "charging_wait": _safe_float(row.get("charging_wait", 0.0)),
        "score": _safe_float(row.get("score", 0.0)),
        "seed": _safe_int(row.get("seed", 0)),
        "solver_backend": solver_backend,
        "solver_status": row.get("solver_status"),
        "exact_reduced_for_license": bool(row.get("exact_reduced_for_license", False)),
        "exact_original_tasks": _safe_int(row.get("exact_original_tasks", 0)),
        "exact_original_vehicles": _safe_int(row.get("exact_original_vehicles", 0)),
        "exact_task_count": _safe_int(row.get("exact_task_count", 0)),
        "exact_vehicle_count": _safe_int(row.get("exact_vehicle_count", 0)),
        "exact_reduction_factor": _safe_float(row.get("exact_reduction_factor", 1.0)),
    }


def _safe_float(value: object, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_int(value: object, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _format_mtime(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        return ""


def run_dashboard(host: str = "127.0.0.1", port: int = 8765) -> None:
    if not WEB_DIR.exists():
        raise FileNotFoundError(f"Web assets not found: {WEB_DIR}")

    server = None
    selected_port = port
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer((host, candidate), DashboardRequestHandler)
            selected_port = candidate
            break
        except OSError:
            continue

    if server is None:
        raise RuntimeError(f"Cannot bind dashboard server on {host}:{port}-{port + 19}")

    url = f"http://{host}:{selected_port}"
    print(f"Dashboard is running at {url}")
    print("Press Ctrl+C to stop.")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
