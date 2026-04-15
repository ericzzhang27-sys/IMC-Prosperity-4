# Assumptions

- The official platform expects a Python `Trader` class with `run(self, state)`.
- `Trader.run` should return `(result, conversions, traderData)`.
- The official platform provides a `datamodel` module; local tests use `local_datamodel.py` only as a fallback.
- `TradingState` may contain `traderData`, but the code handles it being missing or empty.
- Sell-side order book volumes may be negative on the official platform, so the code does not depend on sell volume sign for quoting logic.
- Exact tutorial position limits were not verified from the live platform, so this baseline uses conservative internal limits of `20` for `EMERALDS` and `TOMATOES`.
- Conversions are left at `0` because official tutorial-round conversion behavior was not verified here.
- If the official platform only accepts a single-file submission, these helpers will need to be folded into `trader.py`.
