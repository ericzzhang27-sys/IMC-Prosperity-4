from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

try:
    from datamodel import Order
except ImportError:
    from local_datamodel import Order


DEFAULT_POSITION_LIMIT = 20


def get_position_limit(
    product: str,
    position_limits: Dict[str, int],
    default_limit: int = DEFAULT_POSITION_LIMIT,
) -> int:
    try:
        limit = int(position_limits.get(product, default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    return max(1, limit)


def max_buy_capacity(current_position: int, position_limit: int) -> int:
    return max(0, position_limit - current_position)


def max_sell_capacity(current_position: int, position_limit: int) -> int:
    return max(0, current_position + position_limit)


def worst_case_position(current_position: int, orders: Iterable[Order]) -> Tuple[int, int]:
    buy_total = 0
    sell_total = 0

    for order in orders:
        quantity = int(order.quantity)
        if quantity > 0:
            buy_total += quantity
        elif quantity < 0:
            sell_total += -quantity

    worst_long = current_position + buy_total
    worst_short = current_position - sell_total
    return worst_long, worst_short


def violates_position_limit(current_position: int, orders: Iterable[Order], position_limit: int) -> bool:
    worst_long, worst_short = worst_case_position(current_position, orders)
    return worst_long > position_limit or worst_short < -position_limit


def clip_orders_to_position_limit(
    orders: Iterable[Order],
    current_position: int,
    position_limit: int,
) -> List[Order]:
    remaining_buy = max_buy_capacity(current_position, position_limit)
    remaining_sell = max_sell_capacity(current_position, position_limit)
    clipped: List[Order] = []

    for order in orders:
        quantity = int(order.quantity)
        price = int(order.price)
        symbol = str(order.symbol)

        if quantity > 0 and remaining_buy > 0:
            allowed = min(quantity, remaining_buy)
            if allowed > 0:
                clipped.append(Order(symbol, price, allowed))
                remaining_buy -= allowed
        elif quantity < 0 and remaining_sell > 0:
            requested = -quantity
            allowed = min(requested, remaining_sell)
            if allowed > 0:
                clipped.append(Order(symbol, price, -allowed))
                remaining_sell -= allowed

    return clipped
