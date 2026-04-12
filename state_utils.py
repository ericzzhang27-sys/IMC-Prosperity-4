from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List


def safe_getattr_or_key(source: Any, name: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _coerce_int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_int(value: Any, default: int = 0) -> int:
    coerced = _coerce_int_or_none(value)
    return default if coerced is None else coerced


def read_timestamp(state: Any) -> int:
    return coerce_int(safe_getattr_or_key(state, "timestamp", 0))


def read_trader_data(state: Any) -> str:
    raw = safe_getattr_or_key(state, "traderData", "")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return str(raw)


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
        positions[str(product)] = coerce_int(value, 0)
    return positions


def read_position(state: Any, product: str) -> int:
    return read_positions(state).get(product, 0)


def read_observations(state: Any) -> Any:
    return safe_getattr_or_key(state, "observations", None)


def read_product_trades(container: Any, product: str) -> List[Any]:
    if not isinstance(container, Mapping):
        return []

    raw = container.get(product, [])
    if isinstance(raw, list):
        return list(raw)
    if isinstance(raw, tuple):
        return list(raw)
    return []


def read_own_trades(state: Any, product: str) -> List[Any]:
    return read_product_trades(safe_getattr_or_key(state, "own_trades", {}), product)


def read_market_trades(state: Any, product: str) -> List[Any]:
    return read_product_trades(safe_getattr_or_key(state, "market_trades", {}), product)


def get_order_book_side(order_depth: Any, side_name: str) -> Dict[int, int]:
    raw = safe_getattr_or_key(order_depth, side_name, {})
    if not isinstance(raw, Mapping):
        return {}

    normalized: Dict[int, int] = {}
    for price, volume in raw.items():
        price_int = _coerce_int_or_none(price)
        volume_int = _coerce_int_or_none(volume)
        if price_int is None or volume_int is None or volume_int == 0:
            continue
        normalized[price_int] = volume_int
    return normalized


def get_buy_orders(order_depth: Any) -> Dict[int, int]:
    return get_order_book_side(order_depth, "buy_orders")


def get_sell_orders(order_depth: Any) -> Dict[int, int]:
    return get_order_book_side(order_depth, "sell_orders")
