from __future__ import annotations

import copy
import json
import webbrowser
from dataclasses import asdict
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.parse import urlparse

from .simulation import SCENARIO_SCALES, WEATHER_MODES, FleetSimulator, build_scenario, run_strategies_for_scenario
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
    strategy_names = list(STRATEGY_REGISTRY.keys())
    rows: List[dict] = []

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

    payload_out = {
        "rows": rows,
        "scales": list(SCENARIO_SCALES.keys()),
        "weather_modes": list(WEATHER_MODES),
        "strategies": strategy_names,
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
