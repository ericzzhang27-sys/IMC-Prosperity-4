from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Tuple

try:
    from datamodel import Order, TradingState
except ImportError:
    from local_datamodel import Order, TradingState


DEFAULT_POSITION_LIMIT = 80

POSITION_LIMITS = {
    "EMERALDS": 80,
    "TOMATOES": 80,
}


@dataclass(frozen=True)
class ProductConfig:
    base_order_size: int = 2
    min_quote_edge: int = 1


PRODUCT_CONFIGS = {
    "EMERALDS": ProductConfig(base_order_size=2, min_quote_edge=1),
    "TOMATOES": ProductConfig(base_order_size=1, min_quote_edge=1),
}


DEFAULT_PRODUCT_CONFIG = ProductConfig(base_order_size=1, min_quote_edge=1)


# Registry mapping products to their trading strategies
STRATEGY_REGISTRY: Dict[str, ProductStrategy] = {
    "EMERALDS": ConservativeMarketMakerStrategy(
        ProductConfig(base_order_size=2, min_quote_edge=1)
    ),
    "TOMATOES": ConservativeMarketMakerStrategy(
        ProductConfig(base_order_size=1, min_quote_edge=1)
    ),
}


def safe_getattr_or_key(source: Any, name: str, default: Any = None) -> Any:
    """
    Safely retrieves an attribute from an object or a key from a mapping.
    If the source is None, returns the default value.
    If the source is a mapping (like a dict), uses .get() to retrieve the key.
    Otherwise, uses getattr() to retrieve the attribute.
    """
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _coerce_int_or_none(value: Any) -> int | None:
    """
    Attempts to convert a value to an integer.
    Returns None if the value is None, empty string, or cannot be converted to int.
    """
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def read_trader_data(state: Any) -> str:
    """
    Reads the trader data from the trading state.
    Returns an empty string if not found or invalid.
    """
    raw = safe_getattr_or_key(state, "traderData", "")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return str(raw)


def read_order_depths(state: Any) -> Dict[str, Any]:
    """
    Reads the order depths from the trading state.
    Returns an empty dict if not found or invalid.
    """
    raw = safe_getattr_or_key(state, "order_depths", {})
    if not isinstance(raw, Mapping):
        return {}
    return dict(raw)


def read_positions(state: Any) -> Dict[str, int]:
    """
    Reads the current positions from the trading state.
    Coerces values to integers, defaulting to 0 if invalid.
    Returns a dict of product to position.
    """
    raw = safe_getattr_or_key(state, "position", {})
    if not isinstance(raw, Mapping):
        return {}

    positions: Dict[str, int] = {}
    for product, value in raw.items():
        coerced = _coerce_int_or_none(value)
        positions[str(product)] = 0 if coerced is None else coerced
    return positions


def get_order_book_side(order_depth: Any, side_name: str) -> Dict[int, int]:
    """
    Extracts buy or sell orders from the order depth.
    Normalizes prices and volumes to integers, skipping invalid entries.
    Returns a dict of price to volume.
    """
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
    """
    Gets the buy orders (bids) from the order depth.
    Returns a dict of price to volume.
    """
    return get_order_book_side(order_depth, "buy_orders")


def get_sell_orders(order_depth: Any) -> Dict[int, int]:
    """
    Gets the sell orders (asks) from the order depth.
    Returns a dict of price to volume.
    """
    return get_order_book_side(order_depth, "sell_orders")


def load_trader_data(raw: Any) -> Dict[str, Any]:
    """
    Loads trader data from a raw string or bytes.
    Parses JSON and returns a dict, or empty dict if invalid.
    """
    if raw in (None, ""):
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str):
        return {}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    if not isinstance(payload, dict):
        return {}
    return payload


def _make_json_safe(value: Any) -> Any:
    """
    Recursively converts a value to be JSON serializable.
    Handles dicts, lists, and converts non-serializable types to strings.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _make_json_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_make_json_safe(inner) for inner in value]
    return str(value)


def dump_trader_data(payload: Mapping[str, Any]) -> str:
    """
    Dumps a dict payload to a compact JSON string.
    Makes the payload JSON safe first.
    Returns "{}" if serialization fails.
    """
    safe_payload = _make_json_safe(dict(payload))
    try:
        return json.dumps(safe_payload, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return "{}"


def get_position_limit(
    product: str,
    position_limits: Dict[str, int],
    default_limit: int = DEFAULT_POSITION_LIMIT,
) -> int:
    """
    Gets the position limit for a product from the limits dict.
    Falls back to default_limit if not found or invalid.
    Ensures the limit is at least 1.
    """
    try:
        limit = int(position_limits.get(product, default_limit))
    except (TypeError, ValueError):
        limit = default_limit
    return max(1, limit)


def max_buy_capacity(current_position: int, position_limit: int) -> int:
    """
    Calculates the maximum quantity that can be bought without exceeding position limit.
    """
    return max(0, position_limit - current_position)


def max_sell_capacity(current_position: int, position_limit: int) -> int:
    """
    Calculates the maximum quantity that can be sold without exceeding position limit.
    """
    return max(0, current_position + position_limit)


def clip_orders_to_position_limit(
    orders: Iterable[Order],
    current_position: int,
    position_limit: int,
) -> List[Order]:
    """
    Clips a list of orders to ensure they don't exceed the position limits.
    Processes buy and sell orders separately, reducing quantities as needed.
    Returns the clipped list of orders.
    """
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


def best_bid(order_depth: Any) -> Tuple[int, int] | None:
    """
    Gets the best bid (highest price) from the order depth.
    Returns (price, volume) or None if no bids.
    """
    buy_orders = get_buy_orders(order_depth)
    if not buy_orders:
        return None
    price = max(buy_orders)
    return price, buy_orders[price]


def best_ask(order_depth: Any) -> Tuple[int, int] | None:
    """
    Gets the best ask (lowest price) from the order depth.
    Returns (price, volume) or None if no asks.
    """
    sell_orders = get_sell_orders(order_depth)
    if not sell_orders:
        return None
    price = min(sell_orders)
    return price, sell_orders[price]


def midpoint(best_bid_price: int | None, best_ask_price: int | None) -> float | None:
    """
    Calculates the midpoint between best bid and ask prices.
    Returns None if either price is None.
    """
    if best_bid_price is None or best_ask_price is None:
        return None
    return (best_bid_price + best_ask_price) / 2.0


def estimate_fair_value(order_depth: Any, previous_fair: float | None = None) -> float | None:
    """
    Estimates the fair value of the asset from the order book.
    Uses the midpoint of best bid and ask, or falls back to previous fair value.
    """
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
    """
    Adjusts order sizes based on current inventory position.
    Reduces sizes when position is imbalanced, sets to 0 when near limit.
    Returns (buy_size, sell_size).
    """
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
    """
    Calculates a passive bid price below fair value, ensuring it's not crossing the spread.
    Returns None if invalid.
    """
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
    """
    Calculates a passive ask price above fair value, ensuring it's not crossing the spread.
    Returns None if invalid.
    """
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
    """
    Builds conservative buy and sell orders for a product.
    Uses passive pricing and inventory-aware sizing.
    Returns (orders, fair_value).
    """
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


class ProductStrategy(ABC):
    """
    Abstract base class for product-specific trading strategies.
    Allows different trading logic per product.
    """

    @abstractmethod
    def build_orders(
        self,
        product: str,
        order_depth: Any,
        current_position: int,
        position_limit: int,
        previous_fair: float | None = None,
    ) -> Tuple[List[Order], float | None]:
        """
        Builds orders for a specific product using this strategy.
        Returns (orders, fair_value).
        """
        pass


class ConservativeMarketMakerStrategy(ProductStrategy):
    """
    Conservative market-making strategy.
    Places passive buy and sell orders around fair value.
    Uses inventory-aware sizing to manage risk.
    """

    def __init__(self, config: ProductConfig):
        """Initialize with product-specific configuration."""
        self.config = config

    def build_orders(
        self,
        product: str,
        order_depth: Any,
        current_position: int,
        position_limit: int,
        previous_fair: float | None = None,
    ) -> Tuple[List[Order], float | None]:
        """Builds conservative market-making orders."""
        return build_conservative_orders(
            product=product,
            order_depth=order_depth,
            current_position=current_position,
            position_limit=position_limit,
            previous_fair=previous_fair,
            config=self.config,
        )


class Trader:
    """
    Main trading bot class that implements the trading strategy.
    """
    def run(self, state: TradingState):
        """
        Main trading logic.
        Processes each product, builds orders, clips to limits, and persists fair values.
        Returns (orders_dict, conversions, trader_data).
        """
        result: Dict[str, List[Order]] = {}
        persisted = load_trader_data(read_trader_data(state))
        fair_values = persisted.get("fair_values", {})
        if not isinstance(fair_values, dict):
            fair_values = {}

        positions = read_positions(state)
        order_depths = read_order_depths(state)

        for product, order_depth in order_depths.items():
            position_limit = get_position_limit(product, POSITION_LIMITS, DEFAULT_POSITION_LIMIT)
            previous_fair = fair_values.get(product)
            try:
                previous_fair_value = float(previous_fair) if previous_fair is not None else None
            except (TypeError, ValueError):
                previous_fair_value = None

            candidate_orders, fair_value = build_conservative_orders(
                product=product,
                order_depth=order_depth,
                current_position=positions.get(product, 0),
                position_limit=position_limit,
                previous_fair=previous_fair_value,
                config=PRODUCT_CONFIGS.get(product, DEFAULT_PRODUCT_CONFIG),
            )

            safe_orders = clip_orders_to_position_limit(
                orders=candidate_orders,
                current_position=positions.get(product, 0),
                position_limit=position_limit,
            )
            result[product] = safe_orders

            if fair_value is not None:
                fair_values[product] = round(float(fair_value), 4)

        trader_data = dump_trader_data(
            {
                "version": 1,
                "fair_values": fair_values,
            }
        )
        conversions = 0
        return result, conversions, trader_data
