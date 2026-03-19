from __future__ import annotations

import argparse
import json
from pathlib import Path

from simulator.gui import _weather_stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Precompute weather stats json for dashboard")
    parser.add_argument("--seed", type=int, default=20260309, help="Base seed")
    parser.add_argument("--no-collaboration", action="store_true", help="Disable multi-vehicle collaboration")
    parser.add_argument("--no-static-cplex", action="store_true", help="Skip static CPLEX rows")
    parser.add_argument("--exact-time-limit", type=int, default=120, help="CPLEX time limit per weather-scale run")
    parser.add_argument("--exact-mip-gap", type=float, default=0.0, help="CPLEX mip gap target")
    parser.add_argument("--output", default="results/weather_stats.json", help="Output json file")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = {
        "seed": args.seed,
        "allow_collaboration": not args.no_collaboration,
        "include_static_cplex": not args.no_static_cplex,
        "exact_time_limit": int(args.exact_time_limit),
        "exact_mip_gap": float(args.exact_mip_gap),
    }
    result = _weather_stats(payload)
    rows = result.get("rows", [])
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"weather stats rows={len(rows)} written to {out_path}")


if __name__ == "__main__":
    main()
