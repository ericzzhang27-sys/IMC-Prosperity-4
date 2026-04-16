from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import re
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from io import StringIO
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.round_1_ash import (  # noqa: E402
    DEFAULT_CONFIG,
    DEFAULT_POSITION_LIMIT,
    OSMIUM_CONFIG_FIELDS,
    POSITION_LIMITS,
    PRODUCT,
    build_osmium_config,
    osmium_config_as_dict,
)

try:  # pragma: no cover - optional dependency at runtime
    import matplotlib.pyplot as plt
except Exception:  # pragma: no cover - graceful fallback when matplotlib is unavailable
    plt = None


DEFAULT_ALGORITHM = ROOT / "round_1_ash.py"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "osmium_tuning"
DEFAULT_DAYS = ["1"]
DEFAULT_HEATMAP_METRIC = "total_pnl"
DEFAULT_RISK_PENALTY = 0.50
DEFAULT_NEAR_LIMIT_RATIO = 0.80
DEFAULT_TOP_N = 10
DEFAULT_SEARCH_MODE = "grid"
DEFAULT_GRID: dict[str, list[Any]] = {
    "center_widening_ticks": [3.0, 4.0, 5.0],
    "aggressive_take_threshold": [3.0, 4.0, 5.0],
}
DEFAULT_FIXED_PARAMS: dict[str, Any] = {}

SECTION_ACTIVITY = "\n\n\nActivities log:\n"
SECTION_TRADES = "\n\n\n\n\nTrade History:\n"
TRADE_RE = re.compile(
    r"""
    \{
    \s*"timestamp":\s*(?P<timestamp>-?\d+),
    \s*"buyer":\s*"(?P<buyer>[^"]*)",
    \s*"seller":\s*"(?P<seller>[^"]*)",
    \s*"symbol":\s*"(?P<symbol>[^"]*)",
    \s*"currency":\s*"(?P<currency>[^"]*)",
    \s*"price":\s*(?P<price>-?\d+),
    \s*"quantity":\s*(?P<quantity>-?\d+),
    \s*\}
    """,
    re.VERBOSE | re.MULTILINE,
)


@dataclass
class SweepDefinition:
    fixed_params: dict[str, Any]
    grid: dict[str, list[Any]]
    x_param: str
    y_param: str


@dataclass
class ParsedLog:
    activity_rows: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    sandbox_entries: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Tune the OSMIUM strategy with the Prosperity 4 backtester.")
    parser.add_argument("--algorithm", type=Path, default=DEFAULT_ALGORITHM, help="Algorithm file to backtest.")
    parser.add_argument(
        "--days",
        nargs="+",
        default=DEFAULT_DAYS,
        help="Backtester day selectors, e.g. 1, 1-0, 1--1 1-0.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for tuning results, logs, and heatmaps.",
    )
    parser.add_argument(
        "--grid-file",
        type=Path,
        help="Optional JSON file containing fixed_params, grid, x_param, and y_param overrides.",
    )
    parser.add_argument(
        "--set",
        dest="set_items",
        action="append",
        default=[],
        help="Fixed parameter override in the form name=value. Repeat as needed.",
    )
    parser.add_argument(
        "--grid",
        dest="grid_items",
        action="append",
        default=[],
        help="Grid override in the form name=v1,v2,v3. Repeat as needed.",
    )
    parser.add_argument(
        "--search-mode",
        choices=["grid", "coarse-to-fine"],
        default=DEFAULT_SEARCH_MODE,
        help="Search mode for parameter tuning.",
    )
    parser.add_argument("--x-param", help="Parameter to use on heatmap columns.")
    parser.add_argument("--y-param", help="Parameter to use on heatmap rows.")
    parser.add_argument(
        "--heatmap-metric",
        default=DEFAULT_HEATMAP_METRIC,
        help="Metric to aggregate in the heatmap cells, e.g. total_pnl or robust_score.",
    )
    parser.add_argument(
        "--match-trades",
        choices=["all", "worse", "none"],
        default="all",
        help="Backtester trade-matching mode.",
    )
    parser.add_argument(
        "--risk-penalty",
        type=float,
        default=DEFAULT_RISK_PENALTY,
        help="Penalty multiplier for robust_score = daily_pnl_mean - risk_penalty * daily_pnl_std.",
    )
    parser.add_argument(
        "--near-limit-ratio",
        type=float,
        default=DEFAULT_NEAR_LIMIT_RATIO,
        help="Absolute position ratio treated as 'near the limit' in summary metrics.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help="How many top runs to print and include in the summary JSON.",
    )
    parser.add_argument(
        "--refine-top-k",
        type=int,
        default=3,
        help="Number of best coarse runs to refine around when using coarse-to-fine.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        help="Optional hard cap on the number of runs to execute.",
    )
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="Skip writing heatmap.png even if matplotlib is available.",
    )
    return parser.parse_args()


def parse_scalar(value: str) -> Any:
    text = value.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def parse_fixed_assignment(item: str) -> tuple[str, Any]:
    if "=" not in item:
        raise ValueError(f"Expected name=value, got {item!r}")
    name, raw_value = item.split("=", 1)
    return name.strip(), parse_scalar(raw_value)


def parse_grid_assignment(item: str) -> tuple[str, list[Any]]:
    if "=" not in item:
        raise ValueError(f"Expected name=v1,v2,v3, got {item!r}")
    name, raw_values = item.split("=", 1)
    parts = [part.strip() for part in raw_values.split(",") if part.strip()]
    if not parts:
        raise ValueError(f"Grid assignment {item!r} does not contain any values")
    return name.strip(), [parse_scalar(part) for part in parts]


def default_sweep_definition() -> SweepDefinition:
    keys = list(DEFAULT_GRID)
    if len(keys) < 2:
        raise ValueError("DEFAULT_GRID must contain at least two parameters for the heatmap")
    return SweepDefinition(
        fixed_params=dict(DEFAULT_FIXED_PARAMS),
        grid={name: list(values) for name, values in DEFAULT_GRID.items()},
        x_param=keys[1],
        y_param=keys[0],
    )


def load_grid_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Grid file must contain a JSON object")
    return payload


def validate_param_names(params: Iterable[str]) -> None:
    invalid = [name for name in params if name not in OSMIUM_CONFIG_FIELDS]
    if invalid:
        valid = ", ".join(OSMIUM_CONFIG_FIELDS)
        raise ValueError(f"Unknown OSMIUM parameters: {', '.join(sorted(invalid))}. Valid fields: {valid}")


def build_sweep_definition(args: argparse.Namespace) -> SweepDefinition:
    sweep = default_sweep_definition()

    if args.grid_file:
        payload = load_grid_file(args.grid_file)
        fixed_params = payload.get("fixed_params", {})
        grid = payload.get("grid", {})
        if fixed_params:
            if not isinstance(fixed_params, dict):
                raise ValueError("grid_file.fixed_params must be a JSON object")
            sweep.fixed_params = dict(fixed_params)
        if grid:
            if not isinstance(grid, dict):
                raise ValueError("grid_file.grid must be a JSON object")
            sweep.grid = {name: list(values) for name, values in grid.items()}
        if payload.get("x_param"):
            sweep.x_param = str(payload["x_param"])
        if payload.get("y_param"):
            sweep.y_param = str(payload["y_param"])

    for item in args.set_items:
        name, value = parse_fixed_assignment(item)
        sweep.fixed_params[name] = value

    for item in args.grid_items:
        name, values = parse_grid_assignment(item)
        sweep.grid[name] = values

    if args.x_param:
        sweep.x_param = args.x_param
    if args.y_param:
        sweep.y_param = args.y_param

    if len(sweep.grid) == 0:
        raise ValueError("At least one grid parameter is required")

    validate_param_names(list(sweep.fixed_params) + list(sweep.grid))
    build_osmium_config(sweep.fixed_params)
    for name, values in sweep.grid.items():
        if len(values) == 0:
            raise ValueError(f"Grid parameter {name!r} does not contain any values")
        for value in values:
            build_osmium_config({name: value})

    if sweep.x_param not in sweep.grid:
        raise ValueError(f"x_param {sweep.x_param!r} must also be present in the grid")
    if sweep.y_param not in sweep.grid:
        raise ValueError(f"y_param {sweep.y_param!r} must also be present in the grid")

    return sweep


def canonical_params(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def sort_key(value: Any) -> tuple[int, float | str]:
    if is_number(value):
        return (0, float(value))
    return (1, str(value))


def build_grid_runs(sweep: SweepDefinition) -> list[dict[str, Any]]:
    grid_names = list(sweep.grid)
    grid_values = [sweep.grid[name] for name in grid_names]
    runs: list[dict[str, Any]] = []
    for combination in itertools.product(*grid_values):
        params = dict(sweep.fixed_params)
        params.update(dict(zip(grid_names, combination, strict=False)))
        runs.append(params)
    return runs


def refine_numeric_values(center: Any, coarse_values: Sequence[Any], default_value: Any) -> list[Any]:
    if not is_number(center):
        return [center]

    numeric_values = sorted({float(value) for value in coarse_values if is_number(value)})
    if len(numeric_values) < 2:
        return [center]

    diffs = [b - a for a, b in zip(numeric_values, numeric_values[1:], strict=False) if b - a > 0]
    if not diffs:
        return [center]

    half_step = min(diffs) / 2.0
    candidates = [float(center) - half_step, float(center), float(center) + half_step]

    if isinstance(default_value, int) and not isinstance(default_value, bool):
        return sorted({int(round(value)) for value in candidates}, key=sort_key)

    return sorted({round(value, 6) for value in candidates}, key=sort_key)


def build_refined_runs(
    coarse_rows: list[dict[str, Any]],
    sweep: SweepDefinition,
    metric_name: str,
    refine_top_k: int,
) -> list[dict[str, Any]]:
    successful = [row for row in coarse_rows if row.get("status") == "ok" and row.get(metric_name) is not None]
    if len(successful) == 0:
        return []

    default_values = osmium_config_as_dict(DEFAULT_CONFIG)
    top_rows = sorted(successful, key=lambda row: float(row[metric_name]), reverse=True)[:refine_top_k]

    refined_runs: list[dict[str, Any]] = []
    seen = {
        canonical_params({name: row[name] for name in set(sweep.grid) | set(sweep.fixed_params)})
        for row in successful
    }

    for row in top_rows:
        local_grid: dict[str, list[Any]] = {}
        for name, values in sweep.grid.items():
            local_grid[name] = refine_numeric_values(row[name], values, default_values[name])

        for combo in itertools.product(*(local_grid[name] for name in local_grid)):
            params = dict(sweep.fixed_params)
            params.update(dict(zip(local_grid, combo, strict=False)))
            key = canonical_params(params)
            if key in seen:
                continue
            seen.add(key)
            refined_runs.append(params)

    return refined_runs


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def create_algorithm_wrapper(output_dir: Path, run_id: int, params: dict[str, Any]) -> Path:
    wrapper_dir = output_dir / "run_algorithms"
    ensure_directory(wrapper_dir)

    wrapper_path = wrapper_dir / f"osmium_run_{run_id:04d}.py"
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
    wrapper_source = "\n".join(
        [
            "from strategies.round_1_ash import Trader as BaseTrader",
            "",
            f"PARAM_OVERRIDES = {payload}",
            "",
            "class Trader(BaseTrader):",
            "    def __init__(self):",
            "        super().__init__(config_overrides=PARAM_OVERRIDES)",
            "",
        ]
    )
    wrapper_path.write_text(wrapper_source, encoding="utf-8")
    return wrapper_path


def run_backtester(
    algorithm: Path,
    days: Sequence[str],
    params: dict[str, Any],
    run_id: int,
    output_dir: Path,
    match_trades: str,
) -> dict[str, Any]:
    logs_dir = output_dir / "run_logs"
    ensure_directory(logs_dir)
    wrapper_path = create_algorithm_wrapper(output_dir, run_id, params)

    run_prefix = f"run_{run_id:04d}"
    log_path = logs_dir / f"{run_prefix}.log"
    stdout_path = logs_dir / f"{run_prefix}.stdout.txt"
    stderr_path = logs_dir / f"{run_prefix}.stderr.txt"

    command = [
        sys.executable,
        "-m",
        "prosperity4bt",
        str(wrapper_path),
        *days,
        "--out",
        str(log_path),
        "--match-trades",
        match_trades,
        "--no-progress",
    ]

    started = time.perf_counter()
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    runtime_sec = time.perf_counter() - started

    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")

    result: dict[str, Any] = {
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "runtime_sec": round(runtime_sec, 3),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "log_path": str(log_path),
        "algorithm_path": str(wrapper_path),
    }

    if completed.returncode != 0:
        result["error_message"] = completed.stderr.strip() or completed.stdout.strip() or "Backtester failed"
        return result

    result["stdout_metrics"] = parse_stdout_metrics(completed.stdout)
    try:
        result["parsed_log"] = parse_backtest_log(log_path)
    except Exception as exc:
        result["status"] = "failed"
        result["error_message"] = f"Failed to parse {log_path.name}: {exc}"

    return result


def parse_stdout_metrics(stdout: str) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("Total profit:"):
            metrics["stdout_total_profit"] = parse_number(stripped.split(":", 1)[1].strip())

    in_risk_block = False
    for line in stdout.splitlines():
        if line.startswith("Risk metrics"):
            in_risk_block = True
            continue
        if not in_risk_block:
            continue
        if not line.strip():
            continue
        if not line.startswith("  "):
            break
        name, raw_value = line.strip().split(":", 1)
        metrics[name] = parse_number(raw_value.strip(), allow_na=True)

    return metrics


def parse_backtest_log(path: Path) -> ParsedLog:
    text = path.read_text(encoding="utf-8")
    if SECTION_ACTIVITY not in text or SECTION_TRADES not in text:
        raise ValueError("Backtest log is missing required sections")

    sandbox_text, activity_and_trade_text = text.split(SECTION_ACTIVITY, 1)
    activity_text, trade_text = activity_and_trade_text.split(SECTION_TRADES, 1)
    sandbox_entries = sandbox_text.count('"timestamp"')

    activity_rows: list[dict[str, Any]] = []
    reader = csv.DictReader(StringIO(activity_text.strip()), delimiter=";")
    for row in reader:
        activity_rows.append(
            {
                "day": int(row["day"]),
                "timestamp": int(row["timestamp"]),
                "product": row["product"],
                "bid_price_1": parse_number(row["bid_price_1"]),
                "bid_volume_1": parse_number(row["bid_volume_1"]),
                "ask_price_1": parse_number(row["ask_price_1"]),
                "ask_volume_1": parse_number(row["ask_volume_1"]),
                "mid_price": parse_number(row["mid_price"]),
                "profit_and_loss": float(row["profit_and_loss"]),
            }
        )

    trades: list[dict[str, Any]] = []
    for match in TRADE_RE.finditer(trade_text):
        trades.append(
            {
                "timestamp": int(match.group("timestamp")),
                "buyer": match.group("buyer"),
                "seller": match.group("seller"),
                "symbol": match.group("symbol"),
                "currency": match.group("currency"),
                "price": int(match.group("price")),
                "quantity": int(match.group("quantity")),
            }
        )

    return ParsedLog(activity_rows=activity_rows, trades=trades, sandbox_entries=sandbox_entries)


def parse_number(raw: str | None, allow_na: bool = False) -> float | int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    if allow_na and text.lower() == "n/a":
        return None
    text = text.replace(",", "")
    lowered = text.lower()
    if lowered in {"inf", "+inf", "infinity", "+infinity"}:
        return float("inf")
    if lowered in {"-inf", "-infinity"}:
        return float("-inf")
    if lowered == "nan":
        return None
    if re.fullmatch(r"[+-]?\d+", text):
        return int(text)
    return float(text)


def classify_own_fill(trade: dict[str, Any], best_bid: float | int | None, best_ask: float | int | None) -> str:
    if trade["buyer"] == "SUBMISSION":
        if best_ask is not None and trade["price"] >= best_ask:
            return "aggressive_buy"
        return "passive_bid"
    if trade["seller"] == "SUBMISSION":
        if best_bid is not None and trade["price"] <= best_bid:
            return "aggressive_sell"
        return "passive_ask"
    return "unknown"


def compute_run_metrics(
    parsed_log: ParsedLog,
    params: dict[str, Any],
    risk_penalty: float,
    near_limit_ratio: float,
    stdout_metrics: dict[str, Any],
) -> dict[str, Any]:
    activity_rows = parsed_log.activity_rows
    if len(activity_rows) == 0:
        raise ValueError("Activity log is empty")

    limit = POSITION_LIMITS.get(PRODUCT, DEFAULT_POSITION_LIMIT)
    osmium_rows = [row for row in activity_rows if row["product"] == PRODUCT]
    book_by_timestamp = {row["timestamp"]: row for row in osmium_rows}

    daily_pnl: dict[int, float] = {}
    daily_osmium_pnl: dict[int, float] = {}
    daily_product_pnl: dict[int, dict[str, float]] = {}

    rows_by_day: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in activity_rows:
        rows_by_day[row["day"]].append(row)

    for day, rows in rows_by_day.items():
        last_timestamp = max(row["timestamp"] for row in rows)
        last_rows = [row for row in rows if row["timestamp"] == last_timestamp]
        day_product_pnl = {row["product"]: float(row["profit_and_loss"]) for row in last_rows}
        daily_product_pnl[day] = day_product_pnl
        daily_pnl[day] = float(sum(day_product_pnl.values()))
        daily_osmium_pnl[day] = float(day_product_pnl.get(PRODUCT, 0.0))

    daily_values = list(daily_pnl.values())
    total_pnl = float(sum(daily_values))
    daily_pnl_mean = float(statistics.mean(daily_values))
    daily_pnl_min = float(min(daily_values))
    daily_pnl_std = float(statistics.pstdev(daily_values)) if len(daily_values) > 1 else 0.0
    robust_score = daily_pnl_mean - (risk_penalty * daily_pnl_std)

    own_trades = [
        trade
        for trade in parsed_log.trades
        if trade["symbol"] == PRODUCT and (trade["buyer"] == "SUBMISSION" or trade["seller"] == "SUBMISSION")
    ]

    fill_counts = defaultdict(int)
    fill_qty = defaultdict(int)
    net_by_timestamp: dict[int, int] = defaultdict(int)
    for trade in own_trades:
        row = book_by_timestamp.get(trade["timestamp"])
        best_bid = row["bid_price_1"] if row else None
        best_ask = row["ask_price_1"] if row else None
        fill_type = classify_own_fill(trade, best_bid, best_ask)
        fill_counts[fill_type] += 1
        fill_qty[fill_type] += trade["quantity"]

        if trade["buyer"] == "SUBMISSION":
            net_by_timestamp[trade["timestamp"]] += trade["quantity"]
        elif trade["seller"] == "SUBMISSION":
            net_by_timestamp[trade["timestamp"]] -= trade["quantity"]

    positions: list[int] = []
    position = 0
    for timestamp in sorted(book_by_timestamp):
        position += net_by_timestamp.get(timestamp, 0)
        positions.append(position)

    average_position = float(statistics.mean(positions)) if positions else 0.0
    max_abs_position = max((abs(pos) for pos in positions), default=0)
    near_limit_fraction = (
        sum(abs(pos) >= near_limit_ratio * limit for pos in positions) / len(positions) if positions else 0.0
    )
    inventory_turnover = float(sum(abs(delta) for delta in net_by_timestamp.values()) / 2.0)

    metrics = {
        "total_pnl": total_pnl,
        "osmium_total_pnl": float(sum(daily_osmium_pnl.values())),
        "daily_pnl_mean": daily_pnl_mean,
        "daily_pnl_min": daily_pnl_min,
        "daily_pnl_std": daily_pnl_std,
        "robust_score": robust_score,
        "day_count": len(daily_pnl),
        "daily_pnl_json": json.dumps(daily_pnl, sort_keys=True),
        "daily_osmium_pnl_json": json.dumps(daily_osmium_pnl, sort_keys=True),
        "daily_product_pnl_json": json.dumps(daily_product_pnl, sort_keys=True),
        "fill_count": len(own_trades),
        "filled_quantity": sum(trade["quantity"] for trade in own_trades),
        "average_position": average_position,
        "max_abs_position": max_abs_position,
        "near_limit_fraction": near_limit_fraction,
        "inventory_turnover": inventory_turnover,
        "aggressive_buy_fills": fill_counts["aggressive_buy"],
        "aggressive_sell_fills": fill_counts["aggressive_sell"],
        "passive_bid_fills": fill_counts["passive_bid"],
        "passive_ask_fills": fill_counts["passive_ask"],
        "aggressive_buy_qty": fill_qty["aggressive_buy"],
        "aggressive_sell_qty": fill_qty["aggressive_sell"],
        "passive_bid_qty": fill_qty["passive_bid"],
        "passive_ask_qty": fill_qty["passive_ask"],
        "sandbox_entry_count": parsed_log.sandbox_entries,
        "activity_row_count": len(activity_rows),
    }
    metrics.update(stdout_metrics)

    row = dict(params)
    row.update(metrics)
    row["status"] = "ok"
    return row


def build_heatmap_cells(
    rows: list[dict[str, Any]],
    x_param: str,
    y_param: str,
    metric_name: str,
) -> tuple[dict[tuple[Any, Any], float | None], list[Any], list[Any]]:
    successful = [row for row in rows if row.get("status") == "ok" and row.get(metric_name) is not None]
    grouped: dict[tuple[Any, Any], list[float]] = defaultdict(list)
    for row in successful:
        grouped[(row[y_param], row[x_param])].append(float(row[metric_name]))

    x_values = sorted({row[x_param] for row in successful}, key=sort_key)
    y_values = sorted({row[y_param] for row in successful}, key=sort_key)
    cells: dict[tuple[Any, Any], float | None] = {}
    for y_value in y_values:
        for x_value in x_values:
            values = grouped.get((y_value, x_value), [])
            cells[(y_value, x_value)] = statistics.mean(values) if values else None

    return cells, x_values, y_values


def heatmap_color(value: float, minimum: float, maximum: float) -> tuple[int, int, int]:
    if math.isclose(maximum, minimum):
        ratio = 1.0
    else:
        ratio = (value - minimum) / (maximum - minimum)

    low = (245, 234, 208)
    high = (33, 102, 172)
    return tuple(int(low[idx] + (high[idx] - low[idx]) * ratio) for idx in range(3))


def render_terminal_heatmap(
    rows: list[dict[str, Any]],
    x_param: str,
    y_param: str,
    metric_name: str,
) -> None:
    cells, x_values, y_values = build_heatmap_cells(rows, x_param, y_param, metric_name)
    if not cells:
        print("No successful runs available for the terminal heatmap.")
        return

    cell_values = [value for value in cells.values() if value is not None]
    min_value = min(cell_values)
    max_value = max(cell_values)

    print(f"\nHeatmap ({metric_name})")
    header = [" " * 14] + [f"{x_param}={value}"[:14].rjust(14) for value in x_values]
    print(" ".join(header))

    for y_value in y_values:
        row_label = f"{y_param}={y_value}"[:14].ljust(14)
        rendered_cells = [row_label]
        for x_value in x_values:
            value = cells.get((y_value, x_value))
            if value is None:
                rendered_cells.append(" " * 14)
                continue
            color = heatmap_color(value, min_value, max_value)
            rendered_cells.append(f"\x1b[48;2;{color[0]};{color[1]};{color[2]}m{value:>14,.0f}\x1b[0m")
        print(" ".join(rendered_cells))


def write_heatmap_html(
    rows: list[dict[str, Any]],
    sweep: SweepDefinition,
    metric_name: str,
    output_path: Path,
) -> None:
    cells, x_values, y_values = build_heatmap_cells(rows, sweep.x_param, sweep.y_param, metric_name)
    successful = [row for row in rows if row.get("status") == "ok" and row.get(metric_name) is not None]
    values = [value for value in cells.values() if value is not None]

    fixed_lines = ", ".join(f"{name}={value}" for name, value in sorted(sweep.fixed_params.items()))
    averaged_params = [
        name for name, grid_values in sweep.grid.items() if name not in {sweep.x_param, sweep.y_param} and len(grid_values) > 1
    ]

    html_parts = [
        "<!doctype html>",
        "<html lang='en'>",
        "<head>",
        "<meta charset='utf-8'>",
        f"<title>OSMIUM tuning heatmap ({escape(metric_name)})</title>",
        "<style>",
        "body{font-family:Segoe UI,Arial,sans-serif;background:#f7f5ef;color:#1f2933;margin:24px;}",
        "h1,h2{margin:0 0 12px;}",
        "p{margin:6px 0 18px;max-width:980px;}",
        "table{border-collapse:collapse;margin-top:18px;background:white;box-shadow:0 10px 25px rgba(0,0,0,.08);}",
        "th,td{border:1px solid #d7d2c8;padding:10px 12px;text-align:center;min-width:92px;}",
        "th{background:#efe8d8;position:sticky;top:0;}",
        ".meta{font-size:14px;color:#4b5563;}",
        ".cell-value{font-weight:700;font-size:14px;display:block;}",
        ".cell-sub{font-size:11px;color:#243b53;opacity:.85;display:block;}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>OSMIUM Tuning Heatmap</h1>",
        f"<p class='meta'>Metric: <strong>{escape(metric_name)}</strong>. Successful runs: {len(successful)}.</p>",
        f"<p class='meta'>Fixed parameters: {escape(fixed_lines or 'none')}.</p>",
        f"<p class='meta'>Averaged over other varying parameters: {escape(', '.join(averaged_params) or 'none')}.</p>",
        "<table>",
        "<thead><tr>",
        f"<th>{escape(sweep.y_param)} \\ {escape(sweep.x_param)}</th>",
    ]

    for x_value in x_values:
        html_parts.append(f"<th>{escape(str(x_value))}</th>")

    html_parts.append("</tr></thead><tbody>")

    min_value = min(values) if values else 0.0
    max_value = max(values) if values else 0.0
    for y_value in y_values:
        html_parts.append("<tr>")
        html_parts.append(f"<th>{escape(str(y_value))}</th>")
        for x_value in x_values:
            value = cells.get((y_value, x_value))
            if value is None:
                html_parts.append("<td></td>")
                continue
            color = heatmap_color(value, min_value, max_value)
            style = f"background: rgb({color[0]}, {color[1]}, {color[2]});"
            html_parts.append(
                f"<td style=\"{style}\"><span class='cell-value'>{value:,.0f}</span><span class='cell-sub'>mean</span></td>"
            )
        html_parts.append("</tr>")

    html_parts.extend(["</tbody></table>", "</body>", "</html>"])
    output_path.write_text("\n".join(html_parts), encoding="utf-8")


def write_heatmap_png(
    rows: list[dict[str, Any]],
    sweep: SweepDefinition,
    metric_name: str,
    output_path: Path,
) -> bool:
    if plt is None:  # pragma: no cover - runtime dependency gate
        return False

    cells, x_values, y_values = build_heatmap_cells(rows, sweep.x_param, sweep.y_param, metric_name)
    if not cells:
        return False

    matrix = [[cells.get((y_value, x_value)) for x_value in x_values] for y_value in y_values]
    if all(all(value is None for value in row) for row in matrix):
        return False

    min_value = min(value for row in matrix for value in row if value is not None)
    fill_value = min_value
    numeric_matrix = [[fill_value if value is None else value for value in row] for row in matrix]

    fig_width = max(7.0, 1.3 * len(x_values) + 2.5)
    fig_height = max(5.0, 0.9 * len(y_values) + 2.0)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    image = ax.imshow(numeric_matrix, cmap="YlGnBu", aspect="auto")
    fig.colorbar(image, ax=ax, shrink=0.85, label=metric_name)

    ax.set_xticks(range(len(x_values)))
    ax.set_yticks(range(len(y_values)))
    ax.set_xticklabels([str(value) for value in x_values], rotation=35, ha="right")
    ax.set_yticklabels([str(value) for value in y_values])
    ax.set_xlabel(sweep.x_param)
    ax.set_ylabel(sweep.y_param)
    ax.set_title(f"OSMIUM tuning heatmap: {metric_name}")

    for y_index, y_value in enumerate(y_values):
        for x_index, x_value in enumerate(x_values):
            value = cells.get((y_value, x_value))
            label = "" if value is None else f"{value:,.0f}"
            ax.text(x_index, y_index, label, ha="center", va="center", fontsize=9, color="#102a43")

    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)
    return True


def write_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    columns = sorted({key for row in rows for key in row})
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_json_summary(
    rows: list[dict[str, Any]],
    sweep: SweepDefinition,
    output_path: Path,
    args: argparse.Namespace,
) -> None:
    successful = [row for row in rows if row.get("status") == "ok"]
    top_by_pnl = sorted(successful, key=lambda row: float(row["total_pnl"]), reverse=True)[: args.top_n]
    top_by_robust = sorted(successful, key=lambda row: float(row["robust_score"]), reverse=True)[: args.top_n]

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "algorithm": str(args.algorithm),
        "days": list(args.days),
        "search_mode": args.search_mode,
        "heatmap_metric": args.heatmap_metric,
        "fixed_params": sweep.fixed_params,
        "grid": sweep.grid,
        "x_param": sweep.x_param,
        "y_param": sweep.y_param,
        "results": rows,
        "top_by_total_pnl": top_by_pnl,
        "top_by_robust_score": top_by_robust,
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def format_params(row: dict[str, Any]) -> str:
    param_names = [name for name in OSMIUM_CONFIG_FIELDS if name in row]
    return ", ".join(f"{name}={row[name]}" for name in param_names)


def print_top_runs(rows: list[dict[str, Any]], top_n: int) -> None:
    successful = [row for row in rows if row.get("status") == "ok"]
    if not successful:
        print("No successful runs to summarize.")
        return

    print("\nTop runs by total_pnl:")
    for index, row in enumerate(sorted(successful, key=lambda r: float(r["total_pnl"]), reverse=True)[:top_n], start=1):
        print(f"{index:>2}. total_pnl={row['total_pnl']:>10,.0f} robust_score={row['robust_score']:>10,.2f} params={format_params(row)}")

    print("\nTop runs by robust_score:")
    for index, row in enumerate(
        sorted(successful, key=lambda r: float(r["robust_score"]), reverse=True)[:top_n],
        start=1,
    ):
        print(f"{index:>2}. robust_score={row['robust_score']:>10,.2f} total_pnl={row['total_pnl']:>10,.0f} params={format_params(row)}")


def main() -> None:
    args = parse_args()
    args.algorithm = args.algorithm.resolve()
    args.output_dir = args.output_dir.resolve()
    ensure_directory(args.output_dir)

    sweep = build_sweep_definition(args)
    rows: list[dict[str, Any]] = []

    run_params = build_grid_runs(sweep)
    if args.max_runs is not None:
        run_params = run_params[: args.max_runs]

    search_batches = [("grid", run_params)]
    if args.search_mode == "coarse-to-fine":
        search_batches = [("coarse", run_params)]

    run_id = 0
    executed_params: set[str] = set()
    for stage_name, batch in search_batches:
        for params in batch:
            key = canonical_params(params)
            if key in executed_params:
                continue
            executed_params.add(key)
            run_id += 1

            print(f"[{run_id}] {stage_name}: {params}")
            backtest_result = run_backtester(
                algorithm=args.algorithm,
                days=args.days,
                params=params,
                run_id=run_id,
                output_dir=args.output_dir,
                match_trades=args.match_trades,
            )

            row = dict(params)
            row["run_id"] = run_id
            row["stage"] = stage_name
            row.update(
                {
                    "status": backtest_result["status"],
                    "returncode": backtest_result["returncode"],
                    "runtime_sec": backtest_result["runtime_sec"],
                    "algorithm_path": backtest_result["algorithm_path"],
                    "stdout_path": backtest_result["stdout_path"],
                    "stderr_path": backtest_result["stderr_path"],
                    "log_path": backtest_result["log_path"],
                }
            )

            if backtest_result["status"] == "ok":
                metrics = compute_run_metrics(
                    parsed_log=backtest_result["parsed_log"],
                    params=params,
                    risk_penalty=args.risk_penalty,
                    near_limit_ratio=args.near_limit_ratio,
                    stdout_metrics=backtest_result["stdout_metrics"],
                )
                row.update(metrics)
            else:
                row["error_message"] = backtest_result.get("error_message")

            rows.append(row)

        if args.search_mode == "coarse-to-fine" and stage_name == "coarse":
            refined = build_refined_runs(rows, sweep, args.heatmap_metric, args.refine_top_k)
            if args.max_runs is not None:
                remaining_capacity = max(0, args.max_runs - len(executed_params))
                refined = refined[:remaining_capacity]
            if refined:
                search_batches.append(("refined", refined))

    csv_path = args.output_dir / "tuning_results.csv"
    json_path = args.output_dir / "tuning_results.json"
    heatmap_html_path = args.output_dir / "heatmap.html"
    heatmap_png_path = args.output_dir / "heatmap.png"

    write_csv(rows, csv_path)
    write_json_summary(rows, sweep, json_path, args)
    write_heatmap_html(rows, sweep, args.heatmap_metric, heatmap_html_path)
    png_written = False if args.no_png else write_heatmap_png(rows, sweep, args.heatmap_metric, heatmap_png_path)

    print_top_runs(rows, args.top_n)
    render_terminal_heatmap(rows, sweep.x_param, sweep.y_param, args.heatmap_metric)

    print("\nWrote:")
    print(f"  CSV: {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"  HTML heatmap: {heatmap_html_path}")
    if png_written:
        print(f"  PNG heatmap: {heatmap_png_path}")
    else:
        print("  PNG heatmap: skipped")


if __name__ == "__main__":
    main()
