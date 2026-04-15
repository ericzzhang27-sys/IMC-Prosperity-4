from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

try:
    from datamodel import Order
except ImportError:
    from local_datamodel import Order

from core.risk import max_buy_capacity, max_sell_capacity
from core.state_utils import get_buy_orders, get_sell_orders


@dataclass(frozen=True)
class ProductConfig:
    base_order_size: int = 2
    min_quote_edge: int = 1


def best_bid(order_depth: Any) -> Optional[Tuple[int, int]]:
    buy_orders = get_buy_orders(order_depth)
    if not buy_orders:
        return None
    price = max(buy_orders)
    return price, buy_orders[price]


def best_ask(order_depth: Any) -> Optional[Tuple[int, int]]:
    sell_orders = get_sell_orders(order_depth)
    if not sell_orders:
        return None
    price = min(sell_orders)
    return price, sell_orders[price]


def midpoint(best_bid_price: int | None, best_ask_price: int | None) -> float | None:
    if best_bid_price is None or best_ask_price is None:
        return None
    return (best_bid_price + best_ask_price) / 2.0


def estimate_fair_value(order_depth: Any, previous_fair: float | None = None) -> float | None:
    bid = best_bid(order_depth)
    ask = best_ask(order_depth)
    if bid is not None and ask is not None:
        return midpoint(bid[0], ask[0])
    return previous_fair


def inventory_aware_quote_size(
    position: int,
    position_limit: int,
    base_order_size: int = 2,
) -> Tuple[int, int]:
    limit = max(1, position_limit)
    buy_size = min(base_order_size, max_buy_capacity(position, limit))
    sell_size = min(base_order_size, max_sell_capacity(position, limit))
    half_limit = max(1, limit // 2)

    if position > 0:
        buy_size = max(0, buy_size - 1)
    elif position < 0:
        sell_size = max(0, sell_size - 1)

    if position >= half_limit:
        buy_size = 0
    if position <= -half_limit:
        sell_size = 0

    return buy_size, sell_size


def passive_bid_price(
    best_bid_price: int,
    best_ask_price: int,
    fair_value: float,
    min_quote_edge: int,
) -> int | None:
    if best_ask_price <= best_bid_price:
        return None

    candidate = min(best_bid_price + 1, math.floor(fair_value - min_quote_edge))
    if candidate >= best_ask_price:
        return None
    return candidate


def passive_ask_price(
    best_bid_price: int,
    best_ask_price: int,
    fair_value: float,
    min_quote_edge: int,
) -> int | None:
    if best_ask_price <= best_bid_price:
        return None

    candidate = max(best_ask_price - 1, math.ceil(fair_value + min_quote_edge))
    if candidate <= best_bid_price:
        return None
    return candidate


def build_conservative_orders(
    product: str,
    order_depth: Any,
    current_position: int,
    position_limit: int,
    previous_fair: float | None = None,
    config: ProductConfig | None = None,
) -> Tuple[List[Order], float | None]:
    active_config = config or ProductConfig()
    bid = best_bid(order_depth)
    ask = best_ask(order_depth)
    fair_value = estimate_fair_value(order_depth, previous_fair)

    if bid is None or ask is None or fair_value is None:
        return [], fair_value

    bid_price = passive_bid_price(bid[0], ask[0], fair_value, active_config.min_quote_edge)
    ask_price = passive_ask_price(bid[0], ask[0], fair_value, active_config.min_quote_edge)
    buy_size, sell_size = inventory_aware_quote_size(
        position=current_position,
        position_limit=position_limit,
        base_order_size=active_config.base_order_size,
    )

    orders: List[Order] = []
    if bid_price is not None and buy_size > 0:
        orders.append(Order(product, bid_price, buy_size))
    if ask_price is not None and sell_size > 0:
        orders.append(Order(product, ask_price, -sell_size))

    return orders, fair_value
