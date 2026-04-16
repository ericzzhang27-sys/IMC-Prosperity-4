# IMC Prosperity 4 Minimal Trader

This repository is a safe, minimal Python trading interface for IMC Prosperity 4. It focuses on correctness, defensive programming, and easy extension rather than strategy sophistication.

## What this code does

- exposes a `Trader` class in `trader.py`
- exposes `run(self, state)` and returns `(result, conversions, traderData)`
- quotes conservatively only when both sides of the book are available
- computes fair value from the best bid and best ask midpoint
- keeps sizes tiny and clips every order against worst-case position limits
- stores only a small JSON payload in `traderData`
- includes alternate trader variants under `strategies/`, with root compatibility wrappers for the most-used entrypoints
  EMERALDS uses a fixed-fair market-maker around `10000`, while TOMATOES uses an inventory-aware reservation-price market-maker
- includes `tools/visualizer.py` for rendering submission logs or CSV data into an HTML report, plus a root `visualizer.py` wrapper
- includes notebooks in `analysis/notebooks/` for exploratory price analysis from the CSV data

## Project layout

- `trader.py`: submission-facing interface and orchestration
- `core/`: shared helpers for persistence, risk limits, state parsing, and quote logic
- `strategies/`: alternate strategy implementations such as `trader1.py` and the round 1 INTARIAN PEPPER variants
- `tools/`: utility scripts, including the HTML visualizer implementation
- `analysis/notebooks/`: research notebooks for market-data exploration
- `reports/`: generated HTML reports from the visualizer
- `docs/`: project assumptions and context notes
- `data/`: local CSV price and trade data
- `logs/`: downloaded submission logs such as `95046.log`
- `tests/test_trader.py`: unit tests and one smoke test built from provided sample schema

## Official vs assumed

The attached context pack was treated as primary project context and its source hierarchy was followed.

Working constraints used here:

- Public tooling and mirrored docs strongly indicate a Python `Trader` class with `run(self, state)`.
- Public tooling and the context pack indicate `run()` should return `(result, conversions, traderData)`.
- The provided sample CSVs confirm tutorial products `EMERALDS` and `TOMATOES`, semicolon-delimited price snapshots, and multi-level bid/ask columns.
- The downloaded submission logs in `logs/` are JSON objects containing `activitiesLog`, `tradeHistory`, and per-timestamp diagnostic `logs`.

Still assumed until verified inside the official platform:

- the exact official `datamodel` class definitions
- whether `TradingState` always includes `traderData`
- the sign convention for sell-side order book volumes
- the exact per-product tutorial position limits
- whether multi-file submissions are accepted directly, or whether the final platform submission must be flattened into one file

## How local compatibility works

`trader.py` tries to import the official platform module first:

```python
from datamodel import Order, TradingState
```

If that import is unavailable locally, it falls back to `local_datamodel.py` so the unit tests can run without the platform.

Once you have the real starter files:

1. Drop the official `datamodel.py` into this project or into the test environment.
2. Re-run the tests.
3. Compare the official `TradingState` fields and order book sign conventions against `state_utils.py`.
4. Update `POSITION_LIMITS` in `trader.py` with verified platform values.
5. If the platform requires a single-file upload, inline the helpers into `trader.py` before submission.

## Safety choices in this baseline

- no trading when the book is empty or one-sided
- no conversions
- no reliance on persistent process memory
- no filesystem or environment-variable assumptions
- no third-party dependencies
- no use of sample data to infer hidden competition mechanics

## Visualizing runs

`visualizer.py` supports two input modes:

- `logs`: JSON submission logs in `logs/` such as `logs/95046.log`
- `csv`: semicolon-delimited local data files in `data/`

By default, it reads the submission logs in `logs/` and writes `reports/visualizer_report.html`.

### Default log workflow

```bash
python visualizer.py
```

This renders an HTML report with:

- total PnL over time for each run
- per-product best bid / best ask / mid-price charts
- submission and market trade markers
- per-product PnL charts
- submission position charts
- summary metrics such as spread, fill averages, drawdown, and trade counts
- short excerpts from non-empty sandbox or lambda logs

### Common options

Render only one product and open the result:

```bash
python visualizer.py --product EMERALDS --open
```

Choose an explicit logs directory and output path:

```bash
python visualizer.py --mode logs --logs-dir logs --output reports/my_report.html
```

Force CSV mode against local sample data:

```bash
python visualizer.py --mode csv --data-dir data --output reports/csv_report.html
```

## Alternate strategy in `strategies/trader1.py`

`strategies/trader1.py` currently trades both tutorial products with separate strategies:

- `EMERALDS`: fixed-fair market making around `10000`, with simple passive quoting and opportunistic fills when the visible book is clearly cheap or rich versus fair
- `TOMATOES`: inventory-aware market making using:
  reservation price `fair_value - theta * inventory`
  adaptive half-spread that widens with inventory
  soft and hard inventory thresholds
  one-tick-inside quoting when it helps reduce inventory without crossing the book

This file is meant for faster experimentation than the more modular `trader.py`. A small root `trader1.py` wrapper is kept so older imports do not break.

## Notebook analysis

`analysis/notebooks/tomatoes_volatility_analysis.ipynb` reads the CSV files in `data/` and currently includes:

- rolling volatility from TOMATOES mid-price returns
- short-horizon trend-persistence analysis using log-return signs

Run the notebook locally in Jupyter to extend the TOMATOES research workflow.

## Tuning OSMIUM locally

`strategies/round_1_ash.py` now exposes its key OSMIUM parameters through the `OsmiumConfig` dataclass. The local tuner backtests many parameter combinations by generating small temporary wrapper algorithms, so the submission strategy itself does not need environment-variable hooks.

Install the Prosperity 4 backtester once:

```bash
python -m pip install -U prosperity4btest
```

Run the default 2-parameter grid over round 1:

```bash
python tune_osmium.py
```

Sweep custom values from the command line:

```bash
python tune_osmium.py `
  --days 1-0 1--1 1 `
  --grid center_widening_ticks=3,4,5 `
  --grid aggressive_take_threshold=3,4,5 `
  --set reservation_deviation_skew=0.6 `
  --x-param aggressive_take_threshold `
  --y-param center_widening_ticks
```

Use coarse-to-fine search:

```bash
python tune_osmium.py --search-mode coarse-to-fine --refine-top-k 3
```

You can also load ranges from a JSON file:

```json
{
  "fixed_params": {
    "reservation_deviation_skew": 0.6,
    "inventory_penalty_per_unit": 0.18
  },
  "grid": {
    "center_widening_ticks": [3.0, 4.0, 5.0],
    "aggressive_take_threshold": [3.0, 4.0, 5.0]
  },
  "x_param": "aggressive_take_threshold",
  "y_param": "center_widening_ticks"
}
```

Save that example somewhere such as `docs/osmium_tuning_grid.json`, then run:

```bash
python tune_osmium.py --grid-file docs/osmium_tuning_grid.json
```

The tuner writes:

- `reports/osmium_tuning/tuning_results.csv`
- `reports/osmium_tuning/tuning_results.json`
- `reports/osmium_tuning/heatmap.html`
- `reports/osmium_tuning/heatmap.png` when `matplotlib` is available
- per-run stdout, stderr, and backtest logs under `reports/osmium_tuning/run_logs/`

Each result row stores the parameter combination, total PnL, per-day PnL, fill counts, reconstructed inventory metrics, and aggressive/passive fill breakdown when those can be inferred from the backtester output.

## Running tests

```bash
python -m unittest discover -s tests -v
```

## What still needs platform verification

- exact official `datamodel` contents and import path
- official return signature and whether anything besides `result`, `conversions`, and `traderData` is required
- official tutorial round position limits for each product
- official sell-side volume sign convention in `OrderDepth.sell_orders`
- whether conversions are available in the tutorial round
- whether multi-file submissions are supported, or a single-file export is required
