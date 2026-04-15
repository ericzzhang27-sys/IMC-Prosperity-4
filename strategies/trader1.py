from __future__ import annotations

import math
from typing import Dict, List, Tuple

try:
    from datamodel import Order, TradingState
except ImportError:
    from local_datamodel import Order, TradingState


POSITION_LIMITS = {
    "EMERALDS": 80,
    "TOMATOES": 80,
}

EMERALDS = "EMERALDS"
EMERALDS_FAIR_VALUE = 10000
EMERALDS_TAKE_EDGE = 1
EMERALDS_PASSIVE_SIZE = 4

TOMATOES = "TOMATOES"
THETA = 0.08
BASE_HALF_SPREAD = 3.0
INV_WIDEN_COEFF = 0.03
SOFT_INVENTORY_THRESHOLD = 20
HARD_INVENTORY_THRESHOLD = 60
TOMATOES_BASE_ORDER_SIZE = 4


def get_position(state: TradingState, product: str) -> int:
    raw_positions = getattr(state, "position", {}) or {}
    try:
        return int(raw_positions.get(product, 0))
    except (TypeError, ValueError):
        return 0


def get_best_bid(order_depth) -> Tuple[int, int] | None:
    buy_orders = getattr(order_depth, "buy_orders", {}) or {}
    if not buy_orders:
        return None
    price = max(buy_orders)
    return int(price), int(buy_orders[price])


def get_best_ask(order_depth) -> Tuple[int, int] | None:
    sell_orders = getattr(order_depth, "sell_orders", {}) or {}
    if not sell_orders:
        return None
    price = min(sell_orders)
    return int(price), int(sell_orders[price])


def clip_buy_size(size: int, inventory: int, position_limit: int) -> int:
    return max(0, min(int(size), position_limit - inventory))


def clip_sell_size(size: int, inventory: int, position_limit: int) -> int:
    return max(0, min(int(size), inventory + position_limit))


def compute_mid_fair_value(best_bid_price: int, best_ask_price: int) -> float:
    return (best_bid_price + best_ask_price) / 2.0


def build_emerald_quotes(best_bid_price: int, best_ask_price: int) -> Tuple[int | None, int | None]:
    bid_quote = min(best_bid_price + 1, EMERALDS_FAIR_VALUE - EMERALDS_TAKE_EDGE)
    ask_quote = max(best_ask_price - 1, EMERALDS_FAIR_VALUE + EMERALDS_TAKE_EDGE)

    if bid_quote >= best_ask_price:
        bid_quote = None
    if ask_quote <= best_bid_price:
        ask_quote = None
    if bid_quote is not None and ask_quote is not None and bid_quote >= ask_quote:
        return None, None

    return bid_quote, ask_quote


def build_emerald_orders(order_depth, current_inventory: int) -> List[Order]:
    best_bid = get_best_bid(order_depth)
    best_ask = get_best_ask(order_depth)
    if best_bid is None or best_ask is None:
        return []

    best_bid_price, _ = best_bid
    best_ask_price, _ = best_ask
    if best_bid_price >= best_ask_price:
        return []

    position_limit = POSITION_LIMITS[EMERALDS]
    orders: List[Order] = []
    projected_inventory = current_inventory

    sell_orders = getattr(order_depth, "sell_orders", {}) or {}
    for ask_price in sorted(sell_orders):
        if ask_price >= EMERALDS_FAIR_VALUE - EMERALDS_TAKE_EDGE:
            break
        visible_size = abs(int(sell_orders[ask_price]))
        buy_size = clip_buy_size(visible_size, projected_inventory, position_limit)
        if buy_size <= 0:
            break
        orders.append(Order(EMERALDS, int(ask_price), buy_size))
        projected_inventory += buy_size

    buy_orders = getattr(order_depth, "buy_orders", {}) or {}
    for bid_price in sorted(buy_orders, reverse=True):
        if bid_price <= EMERALDS_FAIR_VALUE + EMERALDS_TAKE_EDGE:
            break
        visible_size = abs(int(buy_orders[bid_price]))
        sell_size = clip_sell_size(visible_size, projected_inventory, position_limit)
        if sell_size <= 0:
            break
        orders.append(Order(EMERALDS, int(bid_price), -sell_size))
        projected_inventory -= sell_size

    bid_quote, ask_quote = build_emerald_quotes(best_bid_price, best_ask_price)
    if bid_quote is not None:
        buy_size = clip_buy_size(EMERALDS_PASSIVE_SIZE, projected_inventory, position_limit)
        if buy_size > 0:
            orders.append(Order(EMERALDS, int(bid_quote), buy_size))
    if ask_quote is not None:
        sell_size = clip_sell_size(EMERALDS_PASSIVE_SIZE, projected_inventory, position_limit)
        if sell_size > 0:
            orders.append(Order(EMERALDS, int(ask_quote), -sell_size))

    return orders


def compute_reservation_price(fair_value: float, inventory: int) -> float:
    return fair_value - (THETA * inventory)


def compute_half_spread(inventory: int) -> float:
    return BASE_HALF_SPREAD + (INV_WIDEN_COEFF * abs(inventory))


def compute_inside_book_quotes(
    best_bid_price: int,
    best_ask_price: int,
) -> Tuple[int | None, int | None]:
    inside_bid = None
    inside_ask = None

    if best_bid_price + 1 < best_ask_price:
        inside_bid = best_bid_price + 1
    if best_ask_price - 1 > best_bid_price:
        inside_ask = best_ask_price - 1

    return inside_bid, inside_ask


def generate_tomato_quotes(
    best_bid_price: int,
    best_ask_price: int,
    fair_value: float,
    inventory: int,
) -> Tuple[int | None, int | None]:
    reservation_price = compute_reservation_price(fair_value, inventory)
    half_spread = compute_half_spread(inventory)
    inside_bid, inside_ask = compute_inside_book_quotes(best_bid_price, best_ask_price)

    bid_quote = math.floor(reservation_price - half_spread)
    ask_quote = math.ceil(reservation_price + half_spread)

    if inventory >= SOFT_INVENTORY_THRESHOLD:
        bid_quote -= 1
        ask_quote -= 1
    elif inventory <= -SOFT_INVENTORY_THRESHOLD:
        bid_quote += 1
        ask_quote += 1

    base_bid_quote = bid_quote
    base_ask_quote = ask_quote

    if inside_bid is not None and inventory <= 0:
        bid_quote = max(bid_quote, inside_bid)
    if inside_ask is not None and inventory >= 0:
        ask_quote = min(ask_quote, inside_ask)

    bid_quote = min(bid_quote, best_ask_price - 1)
    ask_quote = max(ask_quote, best_bid_price + 1)

    if inventory >= HARD_INVENTORY_THRESHOLD:
        bid_quote = None
    if inventory <= -HARD_INVENTORY_THRESHOLD:
        ask_quote = None

    if bid_quote is not None and ask_quote is not None and bid_quote >= ask_quote:
        if inventory > 0:
            bid_quote = None
        elif inventory < 0:
            ask_quote = None
        else:
            bid_quote = base_bid_quote
            ask_quote = base_ask_quote

    return bid_quote, ask_quote


def build_tomato_orders(order_depth, current_inventory: int) -> List[Order]:
    best_bid = get_best_bid(order_depth)
    best_ask = get_best_ask(order_depth)
    if best_bid is None or best_ask is None:
        return []

    best_bid_price, _ = best_bid
    best_ask_price, _ = best_ask
    if best_bid_price >= best_ask_price:
        return []

    fair_value = compute_mid_fair_value(best_bid_price, best_ask_price)
    bid_quote, ask_quote = generate_tomato_quotes(
        best_bid_price=best_bid_price,
        best_ask_price=best_ask_price,
        fair_value=fair_value,
        inventory=current_inventory,
    )

    position_limit = POSITION_LIMITS[TOMATOES]
    orders: List[Order] = []
    if bid_quote is not None:
        buy_size = clip_buy_size(TOMATOES_BASE_ORDER_SIZE, current_inventory, position_limit)
        if buy_size > 0:
            orders.append(Order(TOMATOES, int(bid_quote), buy_size))

    if ask_quote is not None:
        sell_size = clip_sell_size(TOMATOES_BASE_ORDER_SIZE, current_inventory, position_limit)
        if sell_size > 0:
            orders.append(Order(TOMATOES, int(ask_quote), -sell_size))

    return orders


class Trader:
    def run(self, state: TradingState):
        orders: Dict[str, List[Order]] = {}
        order_depths = getattr(state, "order_depths", {}) or {}

        emerald_depth = order_depths.get(EMERALDS)
        if emerald_depth is not None:
            orders[EMERALDS] = build_emerald_orders(emerald_depth, get_position(state, EMERALDS))

        tomatoes_depth = order_depths.get(TOMATOES)
        if tomatoes_depth is not None:
            orders[TOMATOES] = build_tomato_orders(tomatoes_depth, get_position(state, TOMATOES))

        return orders, 0, ""
