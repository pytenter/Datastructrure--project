# EV Fleet Dispatch Simulator (Web Visualization)

This project simulates dynamic dispatch for a new-energy logistics fleet.
It supports multiple online scheduling strategies, charging-station queue pressure,
optional multi-vehicle collaboration, and dynamic-vs-static comparison.

## What is included

- Dynamic task generation and online dispatch simulation
- Fleet constraints: battery, load, speed, consumption, availability
- Charging station constraints: waiting queue and station load
- Web replay dashboard (routes, moving vehicles, charger icons, live panels)
- Strategy benchmarking on `small`, `medium`, `large` scenarios
- Optional static full-information baseline (Gurobi / CPLEX / BnB fallback)

## Strategies

- `nearest_task_first`
- `max_task_first`
- `urgency_distance`
- `auction_multi_agent`
- `metaheuristic_sa`
- `reinforcement_q`
- `hyper_heuristic_ucb`

## 1) Environment

- Python `3.10+` (recommended)

## 2) Dependencies to install

### Required (for dynamic simulation + web dashboard)

No third-party Python package is required by default.
The current code path runs on Python standard library only.

### Optional (only for static exact solver comparison)

Install these only if you want exact/static optimization backends:

- Gurobi path:
  - `pip install gurobipy`
  - Requires a valid Gurobi license
- CPLEX path:
  - `pip install docplex cplex`
  - Requires local CPLEX runtime/license

You can also install optional solver dependencies from:

```bash
pip install -r requirements-optional-solvers.txt
```

## 3) Quick start (team setup)

```bash
# 1) create and activate venv
python -m venv .venv

# Windows PowerShell
.venv\Scripts\Activate.ps1

# 2) upgrade pip
python -m pip install --upgrade pip
```

## 4) Run commands

### Launch dashboard

```bash
python dashboard.py
```

Default URL:

- `http://127.0.0.1:8765`

### Run dynamic-only benchmark

```bash
python main.py --no-oracle --allow-collaboration --output results/summary_dynamic.json
```

### Run dynamic vs static (Gurobi)

```bash
python main.py --allow-collaboration --exact-backend gurobi --exact-scales small --output results/summary_gurobi.json
```

### Run dynamic vs static (CPLEX)

```bash
python main.py --allow-collaboration --exact-backend cplex --exact-scales small --output results/summary_cplex.json
```

### Run fallback static backend (BnB, no commercial solver)

```bash
python main.py --allow-collaboration --exact-backend bnb --exact-scales small medium large --output results/summary_bnb.json
```

### Visualize benchmark outputs in dashboard

1. Run one or more benchmark commands above to generate `results/summary_*.json`.
2. Launch dashboard:

```bash
python dashboard.py
```

3. In the **Benchmark Visualization** panel, click **Refresh Data** and use dataset/scenario/metric filters.

## 5) Main outputs

- `results/summary.json`: overall strategy results
- `results/events.json`: replay events (when `--export-events` is enabled)
- `results/comparison_report.md`: auto-generated comparison report

## 6) Collaboration notes

- Keep the same Python major/minor version across teammates.
- Use a fixed `--seed` for reproducible strategy ranking.
- If one machine has no solver license, use `--exact-backend bnb` to keep workflows aligned.
