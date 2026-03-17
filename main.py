from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List

from simulator.exact_solver import HAS_CPLEX, HAS_GUROBI, solve_with_cplex, solve_with_gurobi
from simulator.simulation import SCENARIO_SCALES, build_scenario, run_strategies_for_scenario
from simulator.static_oracle import solve_static_oracle_bnb
from simulator.strategies import (
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EV fleet collaborative dispatch simulator")
    parser.add_argument(
        "--scales",
        nargs="+",
        default=["small", "medium", "large"],
        help="Scenario scales: small medium large",
    )
    parser.add_argument("--seed", type=int, default=20260309, help="Random seed")
    parser.add_argument(
        "--allow-collaboration",
        action="store_true",
        help="Allow multiple vehicles to serve one overweight task",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=list(STRATEGY_REGISTRY.keys()),
        help="Strategies to run",
    )

    parser.add_argument("--no-oracle", action="store_true", help="Disable static full-information comparison")
    parser.add_argument(
        "--exact-backend",
        choices=["gurobi", "cplex", "bnb"],
        default="gurobi",
        help="Static solver backend. gurobi/cplex are exact MIP backends, bnb is fallback.",
    )
    parser.add_argument(
        "--exact-scales",
        nargs="+",
        default=["small"],
        help="Scales for exact/static comparison, e.g. small medium",
    )
    parser.add_argument("--exact-time-limit", type=int, default=120, help="Static exact solver time limit in seconds")
    parser.add_argument("--exact-mip-gap", type=float, default=0.0, help="Static exact solver mip gap target")

    parser.add_argument(
        "--output",
        default="results/summary.json",
        help="Output json file for summary results",
    )
    parser.add_argument("--export-events", action="store_true", help="Export dispatch events into a dedicated json file")
    parser.add_argument("--events-output", default="results/events.json", help="Path to event json output")
    parser.add_argument("--include-task-results", action="store_true", help="Include per-task details in summary json")

    parser.add_argument("--no-report", action="store_true", help="Disable markdown comparison report generation")
    parser.add_argument("--report-output", default="results/comparison_report.md", help="Markdown report output path")
    return parser.parse_args()


def ensure_valid_scales(scales: Iterable[str]) -> List[str]:
    valid = []
    for scale in scales:
        if scale not in SCENARIO_SCALES:
            raise ValueError(f"Unknown scale '{scale}'. Valid options: {', '.join(SCENARIO_SCALES.keys())}")
        valid.append(scale)
    return valid


def ensure_valid_strategies(strategy_names: Iterable[str]) -> List[str]:
    names = []
    for name in strategy_names:
        if name not in STRATEGY_REGISTRY:
            raise ValueError(f"Unknown strategy '{name}'. Valid options: {', '.join(STRATEGY_REGISTRY.keys())}")
        names.append(name)
    return names


def build_strategy_instances(names: List[str], seed: int) -> list:
    strategies = []
    for idx, name in enumerate(names):
        cls = STRATEGY_REGISTRY[name]
        if name in {"metaheuristic_sa", "reinforcement_q", "hyper_heuristic_ucb"}:
            strategies.append(cls(seed=seed + idx))
        else:
            strategies.append(cls())
    return strategies


def print_table(rows: List[dict]) -> None:
    headers = [
        "scenario",
        "strategy",
        "mode",
        "completed",
        "unserved",
        "overtime",
        "distance",
        "avg_resp",
        "charge_wait",
        "score",
    ]
    widths = {h: len(h) for h in headers}
    for row in rows:
        widths["scenario"] = max(widths["scenario"], len(str(row.get("scenario", "-"))))
        widths["strategy"] = max(widths["strategy"], len(str(row.get("strategy", "-"))))
        widths["mode"] = max(widths["mode"], len(str(row.get("mode", "-"))))
        widths["completed"] = max(widths["completed"], len(str(row.get("completed", "-"))))
        widths["unserved"] = max(widths["unserved"], len(str(row.get("unserved", "-"))))
        widths["overtime"] = max(widths["overtime"], len(str(row.get("overtime", "-"))))
        widths["distance"] = max(widths["distance"], len(f"{row.get('distance', 0.0):.2f}"))
        widths["avg_resp"] = max(widths["avg_resp"], len(f"{row.get('avg_response_time', 0.0):.2f}"))
        widths["charge_wait"] = max(widths["charge_wait"], len(f"{row.get('charging_wait', 0.0):.2f}"))
        widths["score"] = max(widths["score"], len(f"{row.get('score', 0.0):.2f}"))

    line = " | ".join(h.ljust(widths[h]) for h in headers)
    sep = "-+-".join("-" * widths[h] for h in headers)
    print(line)
    print(sep)

    for row in rows:
        print(
            " | ".join(
                [
                    str(row.get("scenario", "-")).ljust(widths["scenario"]),
                    str(row.get("strategy", "-")).ljust(widths["strategy"]),
                    str(row.get("mode", "-")).ljust(widths["mode"]),
                    str(row.get("completed", "-")).rjust(widths["completed"]),
                    str(row.get("unserved", "-")).rjust(widths["unserved"]),
                    str(row.get("overtime", "-")).rjust(widths["overtime"]),
                    f"{row.get('distance', 0.0):.2f}".rjust(widths["distance"]),
                    f"{row.get('avg_response_time', 0.0):.2f}".rjust(widths["avg_resp"]),
                    f"{row.get('charging_wait', 0.0):.2f}".rjust(widths["charge_wait"]),
                    f"{row.get('score', 0.0):.2f}".rjust(widths["score"]),
                ]
            )
        )


def _write_report(path: Path, rows: List[dict]) -> None:
    lines: List[str] = []
    lines.append("# 新能源物流调度对比报告")
    lines.append("")

    scenarios = sorted({row["scenario"] for row in rows})
    for scenario in scenarios:
        lines.append(f"## 场景: {scenario}")
        dyn = [row for row in rows if row["scenario"] == scenario and row["mode"] == "dynamic"]
        dyn = sorted(dyn, key=lambda item: item["score"], reverse=True)
        exact_rows = [row for row in rows if row["scenario"] == scenario and row["mode"].startswith("static")]

        lines.append("### 动态策略排名")
        lines.append("| 排名 | 策略 | 分数 | 完成 | 超时 | 未完成 |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for idx, row in enumerate(dyn, start=1):
            lines.append(f"| {idx} | {row['strategy']} | {row['score']:.2f} | {row['completed']} | {row['overtime']} | {row['unserved']} |")

        if exact_rows:
            exact = exact_rows[0]
            best_dyn = dyn[0] if dyn else None
            lines.append("")
            lines.append("### 静态全信息求解")
            lines.append(f"- 求解模式: `{exact['mode']}`")
            if "solver_backend" in exact:
                lines.append(f"- 后端: `{exact['solver_backend']}`")
            if "solver_status" in exact:
                lines.append(f"- 状态: `{exact['solver_status']}`")
            if "solver_optimal" in exact:
                lines.append(f"- 是否证明最优: `{exact['solver_optimal']}`")
            if "solver_gap" in exact and exact["solver_gap"] is not None:
                lines.append(f"- MIP Gap: `{exact['solver_gap']:.6f}`")
            if "solver_runtime_sec" in exact:
                lines.append(f"- 求解耗时: `{exact['solver_runtime_sec']:.2f}s`")
            if best_dyn is not None:
                score_gap = exact["score"] - best_dyn["score"]
                lines.append(f"- 与最佳动态策略分差(静态-动态): `{score_gap:.2f}`")

        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    scales = ensure_valid_scales(args.scales)
    strategy_names = ensure_valid_strategies(args.strategies)
    exact_scales = ensure_valid_scales(args.exact_scales)

    all_rows: List[dict] = []
    all_raw: List[dict] = []
    all_events: Dict[str, List[dict]] = {}

    for idx, scale in enumerate(scales):
        scenario_seed = args.seed + idx * 100
        scenario = build_scenario(
            scale_name=scale,
            seed=scenario_seed,
            allow_collaboration=args.allow_collaboration,
        )
        strategies = build_strategy_instances(strategy_names, seed=scenario_seed)
        outputs = run_strategies_for_scenario(scenario, strategies)

        for summary, results, events in outputs:
            row = {
                "scenario": summary.scenario,
                "strategy": summary.strategy,
                "mode": "dynamic",
                "completed": summary.completed_tasks,
                "unserved": summary.unserved_tasks,
                "overtime": summary.overtime_tasks,
                "distance": summary.total_distance,
                "avg_response_time": summary.avg_response_time,
                "charging_wait": summary.total_charging_wait,
                "score": summary.final_score,
                "station_utilization": summary.station_utilization,
                "seed": scenario_seed,
                "allow_collaboration": args.allow_collaboration,
            }
            if args.include_task_results:
                row["task_results"] = [asdict(item) for item in results]
            all_rows.append(row)
            all_raw.append(row)

            if args.export_events:
                key = f"{scale}:{summary.strategy}"
                all_events[key] = [asdict(event) for event in events]

        if not args.no_oracle and scale in exact_scales:
            if args.exact_backend == "gurobi" and HAS_GUROBI:
                try:
                    exact = solve_with_gurobi(
                        scenario,
                        time_limit_sec=args.exact_time_limit,
                        mip_gap=args.exact_mip_gap,
                    )
                    exact_row = {
                        "scenario": scale,
                        "strategy": "static_exact_fullinfo",
                        "mode": "static_exact_gurobi",
                        "completed": exact.completed,
                        "unserved": exact.unserved,
                        "overtime": exact.overtime,
                        "distance": exact.total_distance,
                        "avg_response_time": exact.avg_response_time,
                        "charging_wait": exact.total_charging_wait,
                        "score": exact.final_score,
                        "seed": scenario_seed,
                        "solver_backend": exact.backend,
                        "solver_status": exact.status,
                        "solver_optimal": exact.optimal,
                        "solver_gap": exact.mip_gap,
                        "solver_runtime_sec": exact.runtime_sec,
                        "solver_objective": exact.objective_value,
                    }
                    all_rows.append(exact_row)
                    all_raw.append(exact_row)
                except Exception as exc:
                    fail_row = {
                        "scenario": scale,
                        "strategy": "static_exact_fullinfo",
                        "mode": "static_exact_gurobi_failed",
                        "completed": 0,
                        "unserved": len(scenario.tasks),
                        "overtime": 0,
                        "distance": 0.0,
                        "avg_response_time": 0.0,
                        "charging_wait": 0.0,
                        "score": -scenario.config.unserved_penalty * len(scenario.tasks),
                        "seed": scenario_seed,
                        "solver_backend": "gurobi",
                        "solver_status": f"FAILED: {exc}",
                    }
                    all_rows.append(fail_row)
                    all_raw.append(fail_row)
            elif args.exact_backend == "cplex" and HAS_CPLEX:
                try:
                    exact = solve_with_cplex(
                        scenario,
                        time_limit_sec=args.exact_time_limit,
                        mip_gap=args.exact_mip_gap,
                    )
                    exact_row = {
                        "scenario": scale,
                        "strategy": "static_exact_fullinfo",
                        "mode": "static_exact_cplex",
                        "completed": exact.completed,
                        "unserved": exact.unserved,
                        "overtime": exact.overtime,
                        "distance": exact.total_distance,
                        "avg_response_time": exact.avg_response_time,
                        "charging_wait": exact.total_charging_wait,
                        "score": exact.final_score,
                        "seed": scenario_seed,
                        "solver_backend": exact.backend,
                        "solver_status": exact.status,
                        "solver_optimal": exact.optimal,
                        "solver_gap": exact.mip_gap,
                        "solver_runtime_sec": exact.runtime_sec,
                        "solver_objective": exact.objective_value,
                    }
                    all_rows.append(exact_row)
                    all_raw.append(exact_row)
                except Exception as exc:
                    fail_row = {
                        "scenario": scale,
                        "strategy": "static_exact_fullinfo",
                        "mode": "static_exact_cplex_failed",
                        "completed": 0,
                        "unserved": len(scenario.tasks),
                        "overtime": 0,
                        "distance": 0.0,
                        "avg_response_time": 0.0,
                        "charging_wait": 0.0,
                        "score": -scenario.config.unserved_penalty * len(scenario.tasks),
                        "seed": scenario_seed,
                        "solver_backend": "cplex",
                        "solver_status": f"FAILED: {exc}",
                    }
                    all_rows.append(fail_row)
                    all_raw.append(fail_row)
            else:
                task_limit = min(8, len(scenario.tasks))
                vehicle_limit = min(5, len(scenario.vehicles))
                bnb = solve_static_oracle_bnb(
                    scenario,
                    task_limit=task_limit,
                    vehicle_limit=vehicle_limit,
                    node_limit=1200000,
                )
                bnb_row = {
                    "scenario": scale,
                    "strategy": f"oracle_static_bnb_{task_limit}",
                    "mode": "static_oracle_bnb",
                    "completed": bnb.completed,
                    "unserved": bnb.unserved,
                    "overtime": bnb.overtime,
                    "distance": bnb.total_distance,
                    "avg_response_time": bnb.avg_response_time,
                    "charging_wait": 0.0,
                    "score": bnb.score,
                    "seed": scenario_seed,
                    "solver_backend": "bnb",
                    "solver_runtime_sec": bnb.runtime_sec,
                    "solver_status": "OPTIMAL" if bnb.expanded_nodes < 1200000 else "CUTOFF",
                }
                all_rows.append(bnb_row)
                all_raw.append(bnb_row)

    print_table(all_rows)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSummary json written to: {out_path}")

    if args.export_events:
        events_path = Path(args.events_output)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(json.dumps(all_events, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Events json written to: {events_path}")

    if not args.no_report:
        report_path = Path(args.report_output)
        _write_report(report_path, all_rows)
        print(f"Comparison report written to: {report_path}")


if __name__ == "__main__":
    main()
