from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Dict, List

try:
    from datamodel import Order, TradingState
except ImportError:
    from local_datamodel import Order, TradingState


INTARIAN_PEPPER_ROOT = "INTARIAN_PEPPER_ROOT"
DEFAULT_POSITION_LIMIT = 80

POSITION_LIMITS = {
    INTARIAN_PEPPER_ROOT: 80,
}


def safe_getattr_or_key(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def coerce_int_or_none(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def read_trader_data(state: Any) -> str:
    raw = safe_getattr_or_key(state, "traderData", "")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return str(raw)


def load_payload(raw: str) -> Dict[str, Any]:
    if raw in ("", None):
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def dump_payload(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return "{}"


def read_order_depths(state: Any) -> Dict[str, Any]:
    raw = safe_getattr_or_key(state, "order_depths", {})
    if not isinstance(raw, Mapping):
        return {}
    return dict(raw)


def read_positions(state: Any) -> Dict[str, int]:
    raw = safe_getattr_or_key(state, "position", {})
    if not isinstance(raw, Mapping):
        return {}

    positions: Dict[str, int] = {}
    for product, value in raw.items():
        positions[str(product)] = coerce_int_or_none(value) or 0
    return positions


def normalize_book_side(order_depth: Any, side_name: str) -> Dict[int, int]:
    raw = safe_getattr_or_key(order_depth, side_name, {})
    if not isinstance(raw, Mapping):
        return {}

    normalized: Dict[int, int] = {}
    for price, volume in raw.items():
        price_int = coerce_int_or_none(price)
        volume_int = coerce_int_or_none(volume)
        if price_int is None or volume_int is None or volume_int == 0:
            continue
        normalized[price_int] = volume_int
    return normalized


def best_bid(order_depth: Any) -> tuple[int, int] | None:
    buy_orders = normalize_book_side(order_depth, "buy_orders")
    if not buy_orders:
        return None
    price = max(buy_orders)
    return price, buy_orders[price]


def best_ask(order_depth: Any) -> tuple[int, int] | None:
    sell_orders = normalize_book_side(order_depth, "sell_orders")
    if not sell_orders:
        return None
    price = min(sell_orders)
    return price, sell_orders[price]


def passive_bid_price(order_depth: Any) -> int | None:
    top_bid = best_bid(order_depth)
    top_ask = best_ask(order_depth)
    if top_ask is None:
        return None

    ask_price, _ = top_ask
    if top_bid is None:
        return ask_price - 1 if ask_price > 1 else None

    bid_price, _ = top_bid
    candidate = bid_price + 1
    if candidate >= ask_price:
        return None
    return candidate


class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {INTARIAN_PEPPER_ROOT: []}
        positions = read_positions(state)
        order_depths = read_order_depths(state)

        position_limit = POSITION_LIMITS.get(INTARIAN_PEPPER_ROOT, DEFAULT_POSITION_LIMIT)
        current_position = positions.get(INTARIAN_PEPPER_ROOT, 0)
        order_depth = order_depths.get(INTARIAN_PEPPER_ROOT)

        if order_depth is not None:
            top_ask = best_ask(order_depth)
            buy_quantity = max(0, position_limit - current_position)
            if top_ask is not None and buy_quantity > 0:
                ask_price, _ = top_ask
                result[INTARIAN_PEPPER_ROOT].append(
                    Order(INTARIAN_PEPPER_ROOT, ask_price, buy_quantity)
                )

        trader_data = dump_payload(
            {
                "version": 1,
                "round_1_buy_hold": {},
            }
        )
        return result, 0, trader_data
