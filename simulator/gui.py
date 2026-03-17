from __future__ import annotations

import copy
import json
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
from urllib.parse import urlparse

from .simulation import SCENARIO_SCALES, FleetSimulator, build_scenario, run_strategies_for_scenario
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
                    },
                }
            )
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
    scale, seed, allow_collaboration = _extract_common_args(payload)
    strategy_name = str(payload.get("strategy", "urgency_distance"))
    strategy = _build_strategy_instance(strategy_name, seed)

    scenario = build_scenario(scale_name=scale, seed=seed, allow_collaboration=allow_collaboration)
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
    scale, seed, allow_collaboration = _extract_common_args(payload)
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
        scenario_seed = seed + idx * 17
        scenario = build_scenario(scale_name=scale, seed=scenario_seed, allow_collaboration=allow_collaboration)
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


def _extract_common_args(payload: dict) -> Tuple[str, int, bool]:
    scale = str(payload.get("scale", "small"))
    if scale not in SCENARIO_SCALES:
        scale = "small"

    try:
        seed = int(payload.get("seed", 20260309))
    except (TypeError, ValueError):
        seed = 20260309

    allow_collaboration = bool(payload.get("allow_collaboration", True))
    return scale, seed, allow_collaboration


def _build_strategy_instance(strategy_name: str, seed: int):
    cls = STRATEGY_REGISTRY.get(strategy_name, UrgencyDistanceStrategy)
    if cls in {SimulatedAnnealingStrategy, ReinforcementLearningDispatchStrategy, HyperHeuristicStrategy}:
        return cls(seed=seed)
    return cls()


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
