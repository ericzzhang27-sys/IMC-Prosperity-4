from __future__ import annotations

import argparse
import bisect
import csv
import html
import io
import json
import math
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, TypeVar


LOG_FILE_GLOB = "*.log"
PRICE_FILE_GLOB = "prices_round_*.csv"
TRADE_FILE_GLOB = "trades_round_*.csv"

SVG_WIDTH = 1100
SVG_HEIGHT = 320
SVG_MARGIN_LEFT = 70
SVG_MARGIN_RIGHT = 20
SVG_MARGIN_TOP = 20
SVG_MARGIN_BOTTOM = 36
MAX_RENDER_POINTS = 3000

PointT = TypeVar("PointT")


@dataclass(frozen=True)
class PricePoint:
    day: int
    timestamp: int
    product: str
    best_bid: float | None
    best_ask: float | None
    mid_price: float | None
    pnl: float | None

    @property
    def sort_key(self) -> tuple[int, int]:
        return self.day, self.timestamp

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


@dataclass(frozen=True)
class TradePoint:
    day: int
    timestamp: int
    product: str
    price: float
    quantity: int
    signed_quantity: int | None
    source: str

    @property
    def sort_key(self) -> tuple[int, int]:
        return self.day, self.timestamp


@dataclass(frozen=True)
class TimeValuePoint:
    day: int
    timestamp: int
    value: float

    @property
    def sort_key(self) -> tuple[int, int]:
        return self.day, self.timestamp


@dataclass(frozen=True)
class DiagnosticEntry:
    timestamp: int
    kind: str
    message: str


@dataclass(frozen=True)
class RunData:
    name: str
    source_label: str
    mode: str
    prices_by_product: Dict[str, List[PricePoint]]
    trades_by_product: Dict[str, List[TradePoint]]
    total_pnl: List[TimeValuePoint]
    positions_by_product: Dict[str, List[TimeValuePoint]]
    diagnostics: List[DiagnosticEntry]


def _to_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _infer_day_from_filename(path: Path) -> int:
    marker = "day_"
    stem = path.stem
    if marker not in stem:
        return 0
    try:
        return int(stem.split(marker, maxsplit=1)[1])
    except ValueError:
        return 0


def discover_files(root: Path, pattern: str) -> List[Path]:
    return sorted(path for path in root.glob(pattern) if path.is_file())


def downsample_points(points: Sequence[PointT], max_points: int) -> List[PointT]:
    if len(points) <= max_points:
        return list(points)
    step = math.ceil(len(points) / max_points)
    sampled = list(points[::step])
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def chart_x(index: int, total_points: int) -> float:
    plot_width = SVG_WIDTH - SVG_MARGIN_LEFT - SVG_MARGIN_RIGHT
    if total_points <= 1:
        return SVG_MARGIN_LEFT + plot_width / 2
    return SVG_MARGIN_LEFT + (index / (total_points - 1)) * plot_width


def chart_y(value: float, minimum: float, maximum: float) -> float:
    plot_height = SVG_HEIGHT - SVG_MARGIN_TOP - SVG_MARGIN_BOTTOM
    if math.isclose(maximum, minimum):
        return SVG_MARGIN_TOP + plot_height / 2
    ratio = (value - minimum) / (maximum - minimum)
    return SVG_MARGIN_TOP + ((1.0 - ratio) * plot_height)


def build_axes(minimum: float, maximum: float) -> str:
    plot_width = SVG_WIDTH - SVG_MARGIN_LEFT - SVG_MARGIN_RIGHT
    plot_height = SVG_HEIGHT - SVG_MARGIN_TOP - SVG_MARGIN_BOTTOM
    left = SVG_MARGIN_LEFT
    top = SVG_MARGIN_TOP
    bottom = SVG_MARGIN_TOP + plot_height

    ticks: List[str] = []
    for tick_index in range(5):
        if math.isclose(maximum, minimum):
            tick_value = minimum
        else:
            tick_value = minimum + ((maximum - minimum) * tick_index / 4)
        y = chart_y(tick_value, minimum, maximum)
        ticks.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_width}" y2="{y:.2f}" class="grid" />'
        )
        ticks.append(
            f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" class="axis-label">{tick_value:.2f}</text>'
        )

    return (
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" class="axis" />'
        f'<line x1="{left}" y1="{bottom}" x2="{left + plot_width}" y2="{bottom}" class="axis" />'
        + "".join(ticks)
    )


def build_day_markers(points: Sequence[PointT]) -> str:
    if not points:
        return ""

    markers: List[str] = []
    total_points = len(points)
    previous_day = getattr(points[0], "day", 0)
    markers.append(
        f'<text x="{SVG_MARGIN_LEFT}" y="{SVG_HEIGHT - 8}" text-anchor="start" class="axis-label">day {previous_day}</text>'
    )

    for index, point in enumerate(points[1:], start=1):
        point_day = getattr(point, "day", 0)
        if point_day == previous_day:
            continue
        previous_day = point_day
        x = chart_x(index, total_points)
        markers.append(
            f'<line x1="{x:.2f}" y1="{SVG_MARGIN_TOP}" x2="{x:.2f}" y2="{SVG_HEIGHT - SVG_MARGIN_BOTTOM}" class="day-break" />'
        )
        markers.append(
            f'<text x="{x + 4:.2f}" y="{SVG_HEIGHT - 8}" text-anchor="start" class="axis-label">day {point_day}</text>'
        )

    return "".join(markers)


def build_polyline(points: Sequence[tuple[float, float]], color: str, stroke_width: int = 2) -> str:
    if not points:
        return ""
    point_text = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    return (
        f'<polyline fill="none" stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-linejoin="round" stroke-linecap="round" points="{point_text}" />'
    )


def compute_padded_bounds(values: Sequence[float | None]) -> tuple[float, float] | None:
    clean_values = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean_values:
        return None

    minimum = min(clean_values)
    maximum = max(clean_values)
    if math.isclose(minimum, maximum):
        padding = max(abs(minimum) * 0.02, 1.0)
    else:
        padding = max((maximum - minimum) * 0.05, 1.0)
    return minimum - padding, maximum + padding


def compute_total_pnl_series(prices_by_product: Dict[str, List[PricePoint]]) -> List[TimeValuePoint]:
    pnl_by_key: Dict[tuple[int, int], float] = {}
    for points in prices_by_product.values():
        for point in points:
            if point.pnl is None:
                continue
            pnl_by_key[point.sort_key] = point.pnl
    return [TimeValuePoint(day=day, timestamp=timestamp, value=value) for (day, timestamp), value in sorted(pnl_by_key.items())]


def build_product_start_map(prices_by_product: Dict[str, List[PricePoint]]) -> Dict[str, tuple[int, int]]:
    start_map: Dict[str, tuple[int, int]] = {}
    for product, points in prices_by_product.items():
        if points:
            start_map[product] = points[0].sort_key
    return start_map


def build_position_series(
    trades_by_product: Dict[str, List[TradePoint]],
    product_start_map: Dict[str, tuple[int, int]],
) -> Dict[str, List[TimeValuePoint]]:
    positions: Dict[str, List[TimeValuePoint]] = {}

    for product, trades in trades_by_product.items():
        submission_trades = [trade for trade in trades if trade.signed_quantity is not None]
        if not submission_trades:
            continue

        series: List[TimeValuePoint] = []
        start_key = product_start_map.get(product)
        if start_key is not None:
            series.append(TimeValuePoint(day=start_key[0], timestamp=start_key[1], value=0.0))

        running_position = 0
        for trade in sorted(submission_trades, key=lambda item: item.sort_key):
            running_position += trade.signed_quantity or 0
            series.append(TimeValuePoint(day=trade.day, timestamp=trade.timestamp, value=float(running_position)))

        positions[product] = series

    return positions


def read_price_points_from_csv(
    paths: Sequence[Path],
    product_filter: set[str] | None = None,
) -> Dict[str, List[PricePoint]]:
    grouped: Dict[str, List[PricePoint]] = {}

    for path in paths:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            for row in reader:
                product = _clean_text(row.get("product", "")).upper()
                if not product:
                    continue
                if product_filter and product not in product_filter:
                    continue

                best_bid = _to_float(row.get("bid_price_1"))
                best_ask = _to_float(row.get("ask_price_1"))
                mid_price = (best_bid + best_ask) / 2.0 if best_bid is not None and best_ask is not None else None

                point = PricePoint(
                    day=_to_int(row.get("day")) or 0,
                    timestamp=_to_int(row.get("timestamp")) or 0,
                    product=product,
                    best_bid=best_bid,
                    best_ask=best_ask,
                    mid_price=mid_price,
                    pnl=_to_float(row.get("profit_and_loss")),
                )
                grouped.setdefault(product, []).append(point)

    for points in grouped.values():
        points.sort(key=lambda point: point.sort_key)

    return grouped


def parse_trade_record(
    record: dict[str, object],
    default_day: int,
) -> TradePoint | None:
    product = _clean_text(record.get("symbol")).upper()
    price = _to_float(record.get("price"))
    quantity = _to_int(record.get("quantity"))
    timestamp = _to_int(record.get("timestamp"))
    if not product or price is None or quantity is None or timestamp is None:
        return None

    buyer = _clean_text(record.get("buyer"))
    seller = _clean_text(record.get("seller"))
    abs_quantity = abs(quantity)

    source = "market"
    signed_quantity: int | None = None
    if buyer == "SUBMISSION" and seller != "SUBMISSION":
        source = "submission_buy"
        signed_quantity = abs_quantity
    elif seller == "SUBMISSION" and buyer != "SUBMISSION":
        source = "submission_sell"
        signed_quantity = -abs_quantity

    return TradePoint(
        day=default_day,
        timestamp=timestamp,
        product=product,
        price=price,
        quantity=abs_quantity,
        signed_quantity=signed_quantity,
        source=source,
    )


def read_trade_points_from_csv(
    paths: Sequence[Path],
    product_filter: set[str] | None = None,
) -> Dict[str, List[TradePoint]]:
    grouped: Dict[str, List[TradePoint]] = {}

    for path in paths:
        inferred_day = _infer_day_from_filename(path)
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            for row in reader:
                point = parse_trade_record(row, inferred_day)
                if point is None:
                    continue
                if product_filter and point.product not in product_filter:
                    continue
                grouped.setdefault(point.product, []).append(point)

    for points in grouped.values():
        points.sort(key=lambda point: point.sort_key)

    return grouped


def parse_activities_log(
    activities_log: str,
    product_filter: set[str] | None = None,
) -> Dict[str, List[PricePoint]]:
    grouped: Dict[str, List[PricePoint]] = {}
    if not activities_log.strip():
        return grouped

    reader = csv.DictReader(io.StringIO(activities_log), delimiter=";")
    for row in reader:
        product = _clean_text(row.get("product")).upper()
        if not product:
            continue
        if product_filter and product not in product_filter:
            continue

        best_bid = _to_float(row.get("bid_price_1"))
        best_ask = _to_float(row.get("ask_price_1"))
        mid_price = (best_bid + best_ask) / 2.0 if best_bid is not None and best_ask is not None else None

        point = PricePoint(
            day=_to_int(row.get("day")) or 0,
            timestamp=_to_int(row.get("timestamp")) or 0,
            product=product,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            pnl=_to_float(row.get("profit_and_loss")),
        )
        grouped.setdefault(product, []).append(point)

    for points in grouped.values():
        points.sort(key=lambda point: point.sort_key)

    return grouped


def parse_diagnostics(entries: Sequence[dict[str, object]] | None) -> List[DiagnosticEntry]:
    diagnostics: List[DiagnosticEntry] = []
    for entry in entries or []:
        timestamp = _to_int(entry.get("timestamp")) or 0
        for kind, key in (("sandbox", "sandboxLog"), ("lambda", "lambdaLog")):
            message = _clean_text(entry.get(key))
            if message:
                diagnostics.append(DiagnosticEntry(timestamp=timestamp, kind=kind, message=message))
    return diagnostics


def load_runs_from_logs(logs_dir: Path, product_filter: set[str] | None = None) -> List[RunData]:
    runs: List[RunData] = []

    for path in discover_files(logs_dir, LOG_FILE_GLOB):
        raw_text = path.read_text(encoding="utf-8").strip()
        if not raw_text:
            continue

        payload = json.loads(raw_text)
        prices_by_product = parse_activities_log(_clean_text(payload.get("activitiesLog")), product_filter)
        if not prices_by_product:
            continue

        product_start_map = build_product_start_map(prices_by_product)
        default_day_map = {product: start_key[0] for product, start_key in product_start_map.items()}

        trades_by_product: Dict[str, List[TradePoint]] = {}
        for record in payload.get("tradeHistory", []):
            point = parse_trade_record(record, default_day_map.get(_clean_text(record.get("symbol")).upper(), 0))
            if point is None:
                continue
            if product_filter and point.product not in product_filter:
                continue
            trades_by_product.setdefault(point.product, []).append(point)

        for points in trades_by_product.values():
            points.sort(key=lambda point: point.sort_key)

        total_pnl = compute_total_pnl_series(prices_by_product)
        positions_by_product = build_position_series(trades_by_product, product_start_map)
        diagnostics = parse_diagnostics(payload.get("logs"))

        run_name = _clean_text(payload.get("submissionId")) or path.stem
        runs.append(
            RunData(
                name=run_name,
                source_label=str(path),
                mode="logs",
                prices_by_product=prices_by_product,
                trades_by_product=trades_by_product,
                total_pnl=total_pnl,
                positions_by_product=positions_by_product,
                diagnostics=diagnostics,
            )
        )

    return runs


def load_run_from_csv(data_dir: Path, product_filter: set[str] | None = None) -> RunData:
    price_files = discover_files(data_dir, PRICE_FILE_GLOB)
    if not price_files:
        raise SystemExit(f"No price files found in {data_dir} matching {PRICE_FILE_GLOB}.")

    trade_files = discover_files(data_dir, TRADE_FILE_GLOB)
    prices_by_product = read_price_points_from_csv(price_files, product_filter)
    if not prices_by_product:
        raise SystemExit("No matching product rows were found in the price data.")

    trades_by_product = read_trade_points_from_csv(trade_files, product_filter)
    product_start_map = build_product_start_map(prices_by_product)
    positions_by_product = build_position_series(trades_by_product, product_start_map)

    return RunData(
        name=data_dir.name,
        source_label=str(data_dir),
        mode="csv",
        prices_by_product=prices_by_product,
        trades_by_product=trades_by_product,
        total_pnl=compute_total_pnl_series(prices_by_product),
        positions_by_product=positions_by_product,
        diagnostics=[],
    )


def compute_drawdown(points: Sequence[TimeValuePoint]) -> float | None:
    if not points:
        return None
    running_peak = points[0].value
    max_drawdown = 0.0
    for point in points:
        running_peak = max(running_peak, point.value)
        max_drawdown = max(max_drawdown, running_peak - point.value)
    return max_drawdown


def format_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def summarise_run(run: RunData) -> List[tuple[str, str]]:
    trade_count = sum(len(points) for points in run.trades_by_product.values())
    submission_trade_count = sum(
        1 for points in run.trades_by_product.values() for trade in points if trade.signed_quantity is not None
    )
    final_pnl = run.total_pnl[-1].value if run.total_pnl else None
    drawdown = compute_drawdown(run.total_pnl)

    return [
        ("Mode", run.mode),
        ("Products", str(len(run.prices_by_product))),
        ("Observations", str(sum(len(points) for points in run.prices_by_product.values()))),
        ("Trades", str(trade_count)),
        ("Submission Trades", str(submission_trade_count)),
        ("Final PnL", format_number(final_pnl)),
        ("Max Drawdown", format_number(drawdown)),
        ("Diagnostics", str(len(run.diagnostics))),
    ]


def weighted_average_price(trades: Sequence[TradePoint]) -> float | None:
    total_qty = sum(trade.quantity for trade in trades)
    if total_qty <= 0:
        return None
    total_notional = sum(trade.price * trade.quantity for trade in trades)
    return total_notional / total_qty


def summarise_product(
    product: str,
    prices: Sequence[PricePoint],
    trades: Sequence[TradePoint],
    positions: Sequence[TimeValuePoint],
) -> List[tuple[str, str]]:
    spreads = [point.spread for point in prices if point.spread is not None]
    mids = [point.mid_price for point in prices if point.mid_price is not None]
    submission_buys = [trade for trade in trades if trade.source == "submission_buy"]
    submission_sells = [trade for trade in trades if trade.source == "submission_sell"]
    final_position = positions[-1].value if positions else None

    return [
        ("Product", product),
        ("Observations", str(len(prices))),
        ("Trades", str(len(trades))),
        ("Submission Buys", str(len(submission_buys))),
        ("Submission Sells", str(len(submission_sells))),
        ("Average Spread", format_number(sum(spreads) / len(spreads) if spreads else None)),
        ("Average Mid", format_number(sum(mids) / len(mids) if mids else None)),
        ("Mid Range", "n/a" if not mids else f"{min(mids):.2f} to {max(mids):.2f}"),
        ("Buy VWAP", format_number(weighted_average_price(submission_buys))),
        ("Sell VWAP", format_number(weighted_average_price(submission_sells))),
        ("Final Position", format_number(final_position, digits=0)),
    ]


def build_summary_card(title: str, rows: Sequence[tuple[str, str]]) -> str:
    table_rows = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>" for label, value in rows
    )
    return f"""
    <section class="summary-card">
      <h3>{html.escape(title)}</h3>
      <table>
        {table_rows}
      </table>
    </section>
    """


def build_trade_markers(
    prices: Sequence[PricePoint],
    trades: Sequence[TradePoint],
    minimum: float,
    maximum: float,
) -> str:
    if not prices or not trades:
        return ""

    keys = [point.sort_key for point in prices]
    circles: List[str] = []

    for trade in trades:
        index = bisect.bisect_left(keys, trade.sort_key)
        index = min(max(index, 0), len(prices) - 1)
        x = chart_x(index, len(prices))
        y = chart_y(trade.price, minimum, maximum)

        radius = min(7.0, 3.0 + abs(trade.quantity) * 0.15)
        if trade.source == "submission_buy":
            marker_class = "trade-dot submission-buy"
            label = "submission buy"
        elif trade.source == "submission_sell":
            marker_class = "trade-dot submission-sell"
            label = "submission sell"
        else:
            marker_class = "trade-dot market-trade"
            label = "market trade"

        title = f"{label} @ {trade.price:.2f} x {trade.quantity}"
        circles.append(
            f'<circle class="{marker_class}" cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}"><title>{html.escape(title)}</title></circle>'
        )

    return "".join(circles)


def build_price_chart(product: str, prices: Sequence[PricePoint], trades: Sequence[TradePoint], min_ts: int | None = None, max_ts: int | None = None, add_zoom: bool = True) -> str:
    if not prices:
        return ""

    # Filter by timestamp
    if min_ts is not None or max_ts is not None:
        prices = [p for p in prices if (min_ts is None or p.timestamp >= min_ts) and (max_ts is None or p.timestamp <= max_ts)]
        trades = [t for t in trades if (min_ts is None or t.timestamp >= min_ts) and (max_ts is None or t.timestamp <= max_ts)]
        if not prices:
            return ""

    sampled_prices = downsample_points(prices, MAX_RENDER_POINTS)
    bounds = compute_padded_bounds(
        [point.best_bid for point in sampled_prices]
        + [point.best_ask for point in sampled_prices]
        + [point.mid_price for point in sampled_prices]
        + [trade.price for trade in trades]
    )
    if bounds is None:
        return ""
    minimum, maximum = bounds

    bid_points = [
        (chart_x(index, len(sampled_prices)), chart_y(point.best_bid, minimum, maximum))
        for index, point in enumerate(sampled_prices)
        if point.best_bid is not None
    ]
    ask_points = [
        (chart_x(index, len(sampled_prices)), chart_y(point.best_ask, minimum, maximum))
        for index, point in enumerate(sampled_prices)
        if point.best_ask is not None
    ]
    mid_points = [
        (chart_x(index, len(sampled_prices)), chart_y(point.mid_price, minimum, maximum))
        for index, point in enumerate(sampled_prices)
        if point.mid_price is not None
    ]

    svg_attrs = (
        ' class="zoomable-chart" onmousedown="startChartDrag(event)" onmousemove="dragChart(event)"'
        ' onmouseup="endChartDrag(event)" onmouseleave="endChartDrag(event)" onwheel="handleChartWheel(event)"'
        ' data-original-viewbox="0 0 {width} {height}"'
        .format(width=SVG_WIDTH, height=SVG_HEIGHT)
        if add_zoom
        else ""
    )

    svg = f"""
    <svg viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img" aria-label="{html.escape(product)} price chart"{svg_attrs}>
      {build_axes(minimum, maximum)}
      {build_day_markers(sampled_prices)}
      {build_polyline(bid_points, "#2f855a")}
      {build_polyline(ask_points, "#c53030")}
      {build_polyline(mid_points, "#1d4ed8", stroke_width=3)}
      {build_trade_markers(sampled_prices, trades, minimum, maximum)}
    </svg>
    """

    controls = ""
    if add_zoom:
        controls = f"""
      <div class=\"chart-controls\">
        <button type=\"button\" onclick=\"zoomChartByFactor(this.parentElement.nextElementSibling, 0.8)\">Zoom In</button>
        <button type=\"button\" onclick=\"zoomChartByFactor(this.parentElement.nextElementSibling, 1.25)\">Zoom Out</button>
        <button type=\"button\" onclick=\"resetZoom(this.parentElement.nextElementSibling)\">Reset Zoom</button>
      </div>
        """

    return f"""
    <section class="chart-card">
      <h3>{html.escape(product)} Prices</h3>
      <div class="legend">
        <span><span class="legend-swatch bid"></span>Best Bid</span>
        <span><span class="legend-swatch ask"></span>Best Ask</span>
        <span><span class="legend-swatch mid"></span>Mid Price</span>
        <span><span class="legend-swatch trade"></span>Trade Marker</span>
      </div>
      {controls}
      {svg}
    </section>
    """


def build_single_series_chart(title: str, points: Sequence[TimeValuePoint], color: str, add_zoom: bool = True) -> str:
    if not points:
        return ""

    sampled_points = downsample_points(points, MAX_RENDER_POINTS)
    bounds = compute_padded_bounds([point.value for point in sampled_points])
    if bounds is None:
        return ""
    minimum, maximum = bounds

    polyline_points = [
        (chart_x(index, len(sampled_points)), chart_y(point.value, minimum, maximum))
        for index, point in enumerate(sampled_points)
    ]

    svg_attrs = (
        ' class="zoomable-chart" onmousedown="startChartDrag(event)" onmousemove="dragChart(event)"'
        ' onmouseup="endChartDrag(event)" onmouseleave="endChartDrag(event)" onwheel="handleChartWheel(event)"'
        ' data-original-viewbox="0 0 {width} {height}"'
        .format(width=SVG_WIDTH, height=SVG_HEIGHT)
        if add_zoom
        else ""
    )

    controls = ""
    if add_zoom:
        controls = f"""
      <div class=\"chart-controls\">
        <button type=\"button\" onclick=\"zoomChartByFactor(this.parentElement.nextElementSibling, 0.8)\">Zoom In</button>
        <button type=\"button\" onclick=\"zoomChartByFactor(this.parentElement.nextElementSibling, 1.25)\">Zoom Out</button>
        <button type=\"button\" onclick=\"resetZoom(this.parentElement.nextElementSibling)\">Reset Zoom</button>
      </div>
        """

    svg = f"""
    <svg viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}" role="img" aria-label="{html.escape(title)}"{svg_attrs}>
      {build_axes(minimum, maximum)}
      {build_day_markers(sampled_points)}
      {build_polyline(polyline_points, color, stroke_width=3)}
    </svg>
    """

    return f"""
    <section class="chart-card">
      <h3>{html.escape(title)}</h3>
      {controls}
      {svg}
    </section>
    """


def build_diagnostics_card(diagnostics: Sequence[DiagnosticEntry], max_entries: int = 12) -> str:
    if not diagnostics:
        return ""

    entries_html: List[str] = []
    for entry in diagnostics[:max_entries]:
        header = f"{entry.kind} log @ {entry.timestamp}"
        entries_html.append(
            f"<article class=\"diagnostic-entry\"><h4>{html.escape(header)}</h4><pre>{html.escape(entry.message)}</pre></article>"
        )

    return f"""
    <section class="chart-card diagnostics-card">
      <h3>Diagnostics</h3>
      {''.join(entries_html)}
    </section>
    """


def build_run_section(run: RunData) -> str:
    sections: List[str] = [
        f'<section class="run-section"><header class="run-header"><h2>{html.escape(run.name)}</h2><p class="run-meta">{html.escape(run.source_label)}</p></header>',
        build_summary_card("Run Summary", summarise_run(run)),
    ]

    total_pnl_chart = build_single_series_chart(f"{run.name} Total PnL", run.total_pnl, "#7c3aed")
    if total_pnl_chart:
        sections.append(total_pnl_chart)

    for product in sorted(run.prices_by_product):
        prices = run.prices_by_product.get(product, [])
        trades = run.trades_by_product.get(product, [])
        positions = run.positions_by_product.get(product, [])

        sections.append('<section class="product-section">')
        sections.append(build_summary_card(f"{product} Metrics", summarise_product(product, prices, trades, positions)))
        sections.append('<div class="product-charts">')
        sections.append(build_price_chart(product, prices, trades))
        sections.append(build_price_chart(f"{product} (0-1000)", prices, trades, 0, 1000, add_zoom=False))

        pnl_points = [
            TimeValuePoint(day=point.day, timestamp=point.timestamp, value=point.pnl)
            for point in prices
            if point.pnl is not None
        ]
        pnl_chart = build_single_series_chart(f"{product} PnL", pnl_points, "#ea580c")
        if pnl_chart:
            sections.append(pnl_chart)

        position_chart = build_single_series_chart(f"{product} Position", positions, "#0f766e")
        if position_chart:
            sections.append(position_chart)

        sections.append("</div></section>")

    diagnostics_card = build_diagnostics_card(run.diagnostics)
    if diagnostics_card:
        sections.append(diagnostics_card)

    sections.append("</section>")
    return "".join(sections)


def build_html_report(runs: Sequence[RunData], mode: str) -> str:
    if mode == "logs":
        lede = "Rendered from JSON submission logs. Each run includes total PnL, per-product prices, trade markers, PnL traces, inferred positions, and diagnostic excerpts."
    else:
        lede = "Rendered from local CSV data. Each run includes per-product prices, trade markers, and any PnL series present in the price snapshots."

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>IMC Prosperity 4 Visualizer</title>
  <style>
    :root {{
      --bg: #f6f5f1;
      --panel: #ffffff;
      --ink: #1f2937;
      --muted: #6b7280;
      --grid: #e5e7eb;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, #fef3c7, transparent 22%),
        radial-gradient(circle at top right, #dbeafe, transparent 26%),
        linear-gradient(180deg, #fafaf9, var(--bg));
    }}
    .page {{
      max-width: 1280px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 2.4rem; }}
    h2 {{ margin: 0; font-size: 1.8rem; }}
    .lede {{
      margin: 0 0 28px;
      color: var(--muted);
      max-width: 900px;
      line-height: 1.5;
    }}
    .run-section {{
      margin-bottom: 42px;
      padding-bottom: 12px;
      border-bottom: 1px solid #e7e5e4;
    }}
    .run-header {{ margin-bottom: 18px; }}
    .run-meta {{
      margin: 4px 0 0;
      color: var(--muted);
      word-break: break-all;
    }}
    .product-section {{
      display: grid;
      grid-template-columns: minmax(240px, 300px) 1fr;
      gap: 18px;
      margin: 24px 0;
      align-items: start;
    }}
    .product-charts {{
      display: grid;
      gap: 18px;
    }}
    .summary-card, .chart-card {{
      background: var(--panel);
      border: 1px solid #e7e5e4;
      border-radius: 18px;
      box-shadow: 0 14px 34px rgba(15, 23, 42, 0.06);
      padding: 18px;
    }}
    .summary-card h3, .chart-card h3 {{ margin-top: 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{
      padding: 8px 0;
      border-bottom: 1px solid #f1f5f9;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      width: 46%;
      font-weight: 600;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      margin-bottom: 12px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .legend-swatch {{
      display: inline-block;
      width: 14px;
      height: 4px;
      border-radius: 999px;
      margin-right: 6px;
      vertical-align: middle;
    }}
    .legend-swatch.bid {{ background: #2f855a; }}
    .legend-swatch.ask {{ background: #c53030; }}
    .legend-swatch.mid {{ background: #1d4ed8; }}
    .legend-swatch.trade {{
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: rgba(245, 158, 11, 0.65);
    }}
    svg {{ width: 100%; height: auto; overflow: visible; }}
    .axis {{ stroke: #475569; stroke-width: 1.1; }}
    .grid {{ stroke: var(--grid); stroke-width: 1; stroke-dasharray: 4 6; }}
    .axis-label {{
      fill: var(--muted);
      font-size: 12px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    }}
    .day-break {{ stroke: #cbd5e1; stroke-width: 1; stroke-dasharray: 2 6; }}
    .trade-dot {{ stroke-width: 1; }}
    .submission-buy {{ fill: rgba(34, 197, 94, 0.55); stroke: rgba(21, 128, 61, 0.7); }}
    .submission-sell {{ fill: rgba(239, 68, 68, 0.5); stroke: rgba(185, 28, 28, 0.7); }}
    .market-trade {{ fill: rgba(245, 158, 11, 0.5); stroke: rgba(146, 64, 14, 0.6); }}
    .diagnostics-card pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #f8fafc;
      padding: 12px;
      border-radius: 10px;
      border: 1px solid #e2e8f0;
      font-size: 0.92rem;
      line-height: 1.35;
      margin: 0;
    }}
    .diagnostic-entry + .diagnostic-entry {{ margin-top: 16px; }}
    .diagnostic-entry h4 {{
      margin: 0 0 8px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .chart-controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 14px;
    }}
    .chart-controls button {{
      border: 1px solid #d1d5db;
      border-radius: 999px;
      background: #ffffff;
      color: var(--ink);
      padding: 8px 12px;
      font: inherit;
      cursor: pointer;
      transition: background 0.15s ease, border-color 0.15s ease;
    }}
    .chart-controls button:hover {{
      background: #f8fafc;
      border-color: #cbd5e1;
    }}
    .zoomable-chart {{
      cursor: grab;
      touch-action: none;
    }}
    .zoomable-chart.dragging {{
      cursor: grabbing;
    }}
    @media (max-width: 980px) {{
      .product-section {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <h1>IMC Prosperity 4 Visualizer</h1>
    <p class="lede">{html.escape(lede)}</p>
    {''.join(build_run_section(run) for run in runs)}
  </main>
  <script>
    function parseViewBox(svg) {
      const raw = svg.getAttribute('viewBox') || '0 0 1100 320';
      return raw.split(/\s+/).map(Number);
    }

    function setViewBox(svg, x, y, width, height) {
      svg.setAttribute('viewBox', `${x} ${y} ${width} ${height}`);
    }

    function clamp(value, min, max) {
      return Math.min(Math.max(value, min), max);
    }

    function getOriginalWidth(svg) {
      const original = svg.dataset.originalViewbox;
      if (!original) {
        return 1100;
      }
      return Number(original.split(/\s+/)[2]) || 1100;
    }

    function handleChartWheel(event) {
      event.preventDefault();
      const svg = event.currentTarget;
      const rect = svg.getBoundingClientRect();
      const [x, y, width, height] = parseViewBox(svg);
      const factor = event.deltaY < 0 ? 0.85 : 1.15;
      const pointerFraction = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      const originalWidth = getOriginalWidth(svg);
      const minWidth = originalWidth * 0.15;
      const newWidth = clamp(width * factor, minWidth, originalWidth);
      const center = x + pointerFraction * width;
      const newX = clamp(center - pointerFraction * newWidth, 0, originalWidth - newWidth);
      setViewBox(svg, newX, y, newWidth, height);
    }

    function startChartDrag(event) {
      if (event.button !== 0) {
        return;
      }
      const svg = event.currentTarget;
      svg._chartDragState = { dragging: true, lastX: event.clientX };
      svg.classList.add('dragging');
    }

    function dragChart(event) {
      const svg = event.currentTarget;
      const state = svg._chartDragState;
      if (!state || !state.dragging) {
        return;
      }
      const [x, y, width, height] = parseViewBox(svg);
      const rect = svg.getBoundingClientRect();
      const originalWidth = getOriginalWidth(svg);
      const deltaX = event.clientX - state.lastX;
      state.lastX = event.clientX;
      const offset = -(deltaX / rect.width) * width;
      const newX = clamp(x + offset, 0, originalWidth - width);
      setViewBox(svg, newX, y, width, height);
    }

    function endChartDrag(event) {
      const svg = event.currentTarget;
      if (svg._chartDragState) {
        svg._chartDragState.dragging = false;
      }
      svg.classList.remove('dragging');
    }

    function resetZoom(svg) {
      const original = svg.dataset.originalViewbox || '0 0 1100 320';
      svg.setAttribute('viewBox', original);
    }

    function zoomChartByFactor(svg, factor) {
      const [x, y, width, height] = parseViewBox(svg);
      const originalWidth = getOriginalWidth(svg);
      const minWidth = originalWidth * 0.15;
      const newWidth = clamp(width * factor, minWidth, originalWidth);
      const center = x + width * 0.5;
      const newX = clamp(center - newWidth * 0.5, 0, originalWidth - newWidth);
      setViewBox(svg, newX, y, newWidth, height);
    }
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an HTML visualizer for Prosperity 4 submission logs or CSV data.")
    parser.add_argument(
        "--mode",
        choices=("logs", "csv", "auto"),
        default="logs",
        help="Input mode. Defaults to submission logs.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=Path("logs"),
        help="Directory containing downloaded JSON submission logs.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Directory containing prices_round_*.csv and trades_round_*.csv files.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("visualizer_report.html"),
        help="Output HTML file path.",
    )
    parser.add_argument(
        "--product",
        action="append",
        default=[],
        help="Optional product filter. Repeat to include multiple products.",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the rendered HTML report in your default browser.",
    )
    return parser.parse_args()


def resolve_mode(args: argparse.Namespace) -> str:
    if args.mode != "auto":
        return args.mode

    logs_dir = args.logs_dir.resolve()
    if discover_files(logs_dir, LOG_FILE_GLOB):
        return "logs"
    return "csv"


def main() -> None:
    args = parse_args()
    mode = resolve_mode(args)
    product_filter = {value.upper() for value in args.product} or None
    output_path = args.output.resolve()

    if mode == "logs":
        logs_dir = args.logs_dir.resolve()
        runs = load_runs_from_logs(logs_dir, product_filter)
        if not runs:
            raise SystemExit(f"No usable JSON log files were found in {logs_dir}.")
    else:
        data_dir = args.data_dir.resolve()
        runs = [load_run_from_csv(data_dir, product_filter)]

    report = build_html_report(runs, mode)
    output_path.write_text(report, encoding="utf-8")
    print(f"Wrote visual report to {output_path}")

    if args.open:
        webbrowser.open(output_path.as_uri())


if __name__ == "__main__":
    main()
