# IMC Prosperity 4 Minimal Trader

This repository is a safe, minimal Python trading interface for IMC Prosperity 4. It focuses on correctness, defensive programming, and easy extension rather than strategy sophistication.

## What this code does

- exposes a `Trader` class in `trader.py`
- exposes `run(self, state)` and returns `(result, conversions, traderData)`
- quotes conservatively only when both sides of the book are available
- computes fair value from the best bid and best ask midpoint
- keeps sizes tiny and clips every order against worst-case position limits
- stores only a small JSON payload in `traderData`

## Project layout

- `trader.py`: submission-facing interface and orchestration
- `strategy.py`: fair value, quote sizing, and conservative order generation
- `state_utils.py`: safe readers for books, positions, trades, observations, and `traderData`
- `risk.py`: hard position guardrails and worst-case fill clipping
- `persistence.py`: plain JSON serialization for `traderData`
- `local_datamodel.py`: local fallback types for testing when official `datamodel` is absent
- `tests/test_trader.py`: unit tests and one smoke test built from provided sample schema

## Official vs assumed

The attached context pack was treated as primary project context and its source hierarchy was followed.

Working constraints used here:

- Public tooling and mirrored docs strongly indicate a Python `Trader` class with `run(self, state)`.
- Public tooling and the context pack indicate `run()` should return `(result, conversions, traderData)`.
- The provided sample CSVs confirm tutorial products `EMERALDS` and `TOMATOES`, semicolon-delimited price snapshots, and multi-level bid/ask columns.

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
