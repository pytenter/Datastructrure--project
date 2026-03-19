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
- Optional static full-information baseline (CPLEX)

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

### Weather and incident simulation in dashboard

- In the top control bar, use **Weather** selector:
  - `normal`
  - `rain`
  - `congestion`
- `rain` and `congestion` both:
  - add map visual effects in replay
  - change traffic multipliers in simulation (longer travel time windows + incident windows)
- Precompute weather stats json (recommended before opening dashboard):

```bash
python precompute_weather_stats.py --seed 20260309 --exact-time-limit 120 --exact-mip-gap 0.0
```

- Dashboard now loads cached weather stats directly from `results/weather_stats.json` on page load.
- In **Weather Stats**, use table filters for:
  - `Table Scale` (`small/medium/large`)
  - `Weather` (`normal/rain/congestion`)

### Run dynamic-only benchmark

```bash
python main.py --no-oracle --allow-collaboration --output results/summary_dynamic.json
```

### Run dynamic vs static (CPLEX)

```bash
python main.py --allow-collaboration --exact-backend cplex --exact-scales small medium large --output results/summary_cplex.json
```

Note:
- For `medium/large`, exact solving is automatically reduced to a license-safe subset when using size-limited CPLEX licenses.
- Result rows are marked as `static_exact_*_reduced`.

### Recommended split run (higher time limit)

When `medium/large` takes too long, run in two stages and merge:

1. Run `small + medium` (higher limit)

```bash
python main.py --scales small medium --allow-collaboration --exact-backend cplex --exact-scales small medium --exact-time-limit 600 --exact-mip-gap 0.02 --output results/summary_cplex_sm.json
```

2. Run `large` only (higher limit)

```bash
python main.py --scales large --allow-collaboration --exact-backend cplex --exact-scales large --exact-time-limit 1200 --exact-mip-gap 0.03 --output results/summary_cplex_large.json
```

3. Merge into one file (for dashboard)

```bash
python -c "import json;from pathlib import Path;sm=json.loads(Path('results/summary_cplex_sm.json').read_text(encoding='utf-8'));lg=json.loads(Path('results/summary_cplex_large.json').read_text(encoding='utf-8'));rows=[r for r in sm if r.get('scenario')!='large']+[r for r in lg if r.get('scenario')=='large'];order={'small':0,'medium':1,'large':2};rows.sort(key=lambda r:(order.get(r.get('scenario'),99),r.get('mode',''),r.get('strategy','')));Path('results/summary_cplex.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');print('merged -> results/summary_cplex.json, rows=',len(rows))"
```

4. View in dashboard

```bash
python dashboard.py
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
- If one machine has no CPLEX runtime/license, static exact rows will be marked as failed and dynamic rows are still generated.
