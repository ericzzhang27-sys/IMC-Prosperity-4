from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, List

try:
    from datamodel import Order, TradingState
except ImportError:
    from local_datamodel import Order, TradingState


PRODUCT = "ASH_COATED_OSMIUM"
FAIR_VALUE = 10000

# Expose the hard limit cleanly so it is easy to tune if the exchange limit is
# confirmed elsewhere.
DEFAULT_POSITION_LIMIT = 80
POSITION_LIMITS = {
    PRODUCT: DEFAULT_POSITION_LIMIT,
}


@dataclass(frozen=True)
class MarketContext:
    product: str
    timestamp: int
    position: int
    position_limit: int
    best_bid_price: int | None
    best_bid_volume: int | None
    best_ask_price: int | None
    best_ask_volume: int | None
    mid_price: float


@dataclass
class StrategyResult:
    orders: List[Order] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OsmiumConfig:
    # Fixed anchor fair value for the mean-reverting OSMIUM thesis.
    fair_value: int = FAIR_VALUE

    # Reservation-price logic.
    # How strongly adjusted fair moves against current mid-price deviation from fair.
    reservation_deviation_skew: float = 0.30
    # Maximum total ticks the deviation component is allowed to move adjusted fair.
    max_deviation_reservation_shift: float = 6.0
    # How many ticks adjusted fair moves per unit of inventory to encourage flattening.
    inventory_penalty_per_unit: float = 0.18
    # Maximum total ticks the inventory component is allowed to move adjusted fair.
    max_inventory_reservation_shift: float = 7.0

    # Passive quoting behavior and center widening / breakout protection.
    # Baseline half-spread before center-defense and stretch-based adjustments.
    base_half_spread: float = 5.0
    # Extra half-spread added when mid is near fair to reduce adverse selection at the center.
    center_widening_ticks: float = 3.0
    # Distance from fair where center-defense is fully active.
    center_widening_band: float = 4.0
    # How quickly the common spread narrows as price moves farther away from fair.
    deviation_narrowing_per_tick: float = 0.30
    # Maximum total narrowing applied from being far away from fair.
    max_deviation_narrowing_ticks: float = 3.0
    # Deviation level where the favored side starts leaning more aggressively for fills.
    moderate_deviation_band: float = 4.0
    # Deviation level where the strategy treats price as strongly stretched from fair.
    stretched_deviation_band: float = 7.0
    # Minimum number of ticks passive quotes must stay on the correct side of fair.
    fair_value_quote_buffer: int = 1

    # Side-specific skew. Favored side gets tighter / larger; unfavored side
    # gets wider / smaller.
    # Per-tick tightening on the mean-reversion-favored side.
    favored_side_tightening_per_tick: float = 0.26
    # Per-tick widening on the unfavored side to reduce bad inventory adds.
    unfavored_side_widening_per_tick: float = 0.30
    # Maximum total tightening allowed on the favored side.
    max_favored_side_tightening: float = 3.0
    # Maximum total widening allowed on the unfavored side.
    max_unfavored_side_widening: float = 3.0
    # Extra tightening on the quote that reduces existing inventory.
    inventory_exit_tightening_ticks: float = 3.0
    # Extra widening on the quote that adds more same-direction inventory.
    inventory_entry_widening_ticks: float = 3.0

    # Quote sizes scale by regime, then inventory and deviation skew them.
    # Passive quote size when price is near fair and the strategy wants to stay defensive.
    center_passive_size: int = 3
    # Default passive quote size outside the center-defense regime.
    base_passive_size: int = 7
    # Passive quote size when price is far from fair and reversion edge is strongest.
    stretched_passive_size: int = 10
    # Extra size added on the favored side in normal off-center regimes.
    favored_side_size_boost: int = 4
    # Additional favored-side size added when deviation is in the stretched regime.
    stretched_favored_side_size_boost: int = 3
    # Size reduction on the unfavored side so inventory capacity is preserved.
    unfavored_side_size_cut: int = 1
    # Size boost on the side that reduces an existing inventory position.
    inventory_exit_size_boost: int = 5
    # Size reduction on the side that would add more same-direction inventory.
    inventory_entry_size_cut: int = 3
    # Hard cap for any single passive quote size.
    max_passive_size: int = 18

    # Optional suppression thresholds for one-sided participation.
    # Price deviation beyond which the strategy can suppress the unfavored side completely.
    quote_suppression_deviation: float = 11.0
    # Inventory usage level required before suppression is allowed.
    quote_suppression_inventory_ratio: float = 0.75
    # Inventory usage level where the strategy becomes effectively reduce-only.
    reduce_only_inventory_ratio: float = 0.94
    # Inventory usage level where same-direction aggressive adds require extreme edge.
    hard_limit_inventory_ratio: float = 0.985

    # Selective aggressive taking: only cross when edge is clearly large.
    # Minimum edge versus adjusted fair before crossing the spread.
    aggressive_take_threshold: float = 2.5
    # Threshold discount when taking on the mean-reversion-favored side.
    aggressive_favored_side_relief: float = 1.5
    # Extra threshold added when a take would increase same-direction inventory.
    aggressive_same_direction_penalty: float = 4.0
    # Additional threshold discount when price is deeply stretched from fair.
    stretched_take_threshold_relief: float = 1.0
    # Minimum edge required to keep adding near hard position limits.
    aggressive_extreme_edge_threshold: float = 8.0
    # Max quantity for one aggressive take at a single price level.
    max_aggressive_take_size: int = 10
    # Max total aggressive quantity per decision step across all price levels.
    max_total_aggressive_take: int = 16


DEFAULT_CONFIG = OsmiumConfig()
OSMIUM_CONFIG_FIELDS = tuple(asdict(DEFAULT_CONFIG).keys())


def osmium_config_as_dict(config: OsmiumConfig | None = None) -> Dict[str, Any]:
    return dict(asdict(config or DEFAULT_CONFIG))


def _coerce_config_value(name: str, value: Any, default_value: Any) -> Any:
    try:
        if isinstance(default_value, bool):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True
                if lowered in {"0", "false", "no", "off"}:
                    return False
            raise ValueError(f"Expected a boolean for {name}")

        if isinstance(default_value, int) and not isinstance(default_value, bool):
            return int(value)

        if isinstance(default_value, float):
            return float(value)

        return value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Could not coerce {name}={value!r} to {type(default_value).__name__}") from exc


def build_osmium_config(overrides: Mapping[str, Any] | None = None) -> OsmiumConfig:
    if not overrides:
        return DEFAULT_CONFIG

    defaults = osmium_config_as_dict(DEFAULT_CONFIG)
    coerced: Dict[str, Any] = {}
    for name, value in overrides.items():
        if name not in defaults:
            valid = ", ".join(OSMIUM_CONFIG_FIELDS)
            raise ValueError(f"Unknown OSMIUM config field {name!r}. Valid fields: {valid}")
        coerced[name] = _coerce_config_value(name, value, defaults[name])

    return replace(DEFAULT_CONFIG, **coerced)


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


def buy_capacity(position: int, position_limit: int) -> int:
    return max(0, position_limit - position)


def sell_capacity(position: int, position_limit: int) -> int:
    return max(0, position + position_limit)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def build_context(state: TradingState, product: str, position_limit: int, config: OsmiumConfig) -> MarketContext | None:
    order_depths = safe_getattr_or_key(state, "order_depths", {})
    if not isinstance(order_depths, Mapping):
        return None

    order_depth = order_depths.get(product)
    if order_depth is None:
        return None

    positions = safe_getattr_or_key(state, "position", {})
    position = 0
    if isinstance(positions, Mapping):
        position = coerce_int_or_none(positions.get(product)) or 0

    top_bid = best_bid(order_depth)
    top_ask = best_ask(order_depth)
    fair = float(config.fair_value)
    if top_bid is not None and top_ask is not None:
        mid_price = (top_bid[0] + top_ask[0]) / 2.0
    elif top_bid is not None:
        mid_price = (top_bid[0] + config.fair_value) / 2.0
    elif top_ask is not None:
        mid_price = (top_ask[0] + config.fair_value) / 2.0
    else:
        mid_price = fair

    return MarketContext(
        product=product,
        timestamp=coerce_int_or_none(safe_getattr_or_key(state, "timestamp", 0)) or 0,
        position=position,
        position_limit=position_limit,
        best_bid_price=top_bid[0] if top_bid is not None else None,
        best_bid_volume=abs(top_bid[1]) if top_bid is not None else None,
        best_ask_price=top_ask[0] if top_ask is not None else None,
        best_ask_volume=abs(top_ask[1]) if top_ask is not None else None,
        mid_price=float(mid_price),
    )


def favored_side_from_deviation(deviation: float) -> str:
    if deviation > 0.5:
        return "sell"
    if deviation < -0.5:
        return "buy"
    return "neutral"


def reservation_price(ctx: MarketContext, config: OsmiumConfig) -> tuple[float, float, float]:
    deviation = ctx.mid_price - config.fair_value

    # Reservation price moves against the current mispricing so the strategy is
    # less willing to buy rich or sell cheap.
    price_component = clamp(
        -config.reservation_deviation_skew * deviation,
        -config.max_deviation_reservation_shift,
        config.max_deviation_reservation_shift,
    )

    # Inventory skew biases the reservation price toward flattening.
    inventory_component = clamp(
        -config.inventory_penalty_per_unit * ctx.position,
        -config.max_inventory_reservation_shift,
        config.max_inventory_reservation_shift,
    )

    adjusted_fair = config.fair_value + price_component + inventory_component
    return adjusted_fair, price_component, inventory_component


def common_half_spread(abs_deviation: float, config: OsmiumConfig) -> float:
    # Near fair we widen to protect against fast breakout adverse selection.
    center_bonus = 0.0
    if abs_deviation <= config.center_widening_band:
        center_bonus = config.center_widening_ticks
    else:
        decay = (abs_deviation - config.center_widening_band) * 0.25
        center_bonus = max(0.0, config.center_widening_ticks - decay)

    # As price stretches away from fair, the reversion-favored side can tighten.
    narrowing = min(
        config.max_deviation_narrowing_ticks,
        max(0.0, abs_deviation - config.center_widening_band) * config.deviation_narrowing_per_tick,
    )
    return max(1.5, config.base_half_spread + center_bonus - narrowing)


def side_offsets(
    favored_side: str,
    deviation: float,
    position_ratio: float,
    position: int,
    config: OsmiumConfig,
) -> tuple[float, float]:
    abs_deviation = abs(deviation)
    bid_offset = 0.0
    ask_offset = 0.0

    # Deviation skew only penalizes the unwanted side. The favored side will
    # compete for queue priority later in passive_quotes via a 1-tick
    # improvement rule instead of a tighter offset here.
    if favored_side == "buy":
        ask_offset += min(config.max_unfavored_side_widening, abs_deviation * config.unfavored_side_widening_per_tick)
    elif favored_side == "sell":
        bid_offset += min(config.max_unfavored_side_widening, abs_deviation * config.unfavored_side_widening_per_tick)

    # Inventory-aware exits: the reducing side tightens, the adding side widens.
    if position > 0:
        ask_offset -= position_ratio * config.inventory_exit_tightening_ticks
        bid_offset += position_ratio * config.inventory_entry_widening_ticks
    elif position < 0:
        bid_offset -= position_ratio * config.inventory_exit_tightening_ticks
        ask_offset += position_ratio * config.inventory_entry_widening_ticks

    return bid_offset, ask_offset


def passive_base_size(abs_deviation: float, config: OsmiumConfig) -> int:
    if abs_deviation <= config.center_widening_band:
        return config.center_passive_size
    if abs_deviation <= config.stretched_deviation_band:
        return config.base_passive_size
    return config.stretched_passive_size


def passive_sizes(
    favored_side: str,
    abs_deviation: float,
    position: int,
    position_limit: int,
    config: OsmiumConfig,
) -> tuple[int, int]:
    base_size = passive_base_size(abs_deviation, config)
    bid_size = base_size
    ask_size = base_size
    position_ratio = abs(position) / position_limit if position_limit > 0 else 1.0

    if favored_side == "buy":
        bid_size += config.favored_side_size_boost
        ask_size -= config.unfavored_side_size_cut
    elif favored_side == "sell":
        ask_size += config.favored_side_size_boost
        bid_size -= config.unfavored_side_size_cut

    # When price is far from fair, lean harder into the reversion-favored side
    # so the strategy wins more queue priority and earns more fills.
    if abs_deviation >= config.stretched_deviation_band:
        if favored_side == "buy":
            bid_size += config.stretched_favored_side_size_boost
        elif favored_side == "sell":
            ask_size += config.stretched_favored_side_size_boost

    inventory_exit_boost = int(round(position_ratio * config.inventory_exit_size_boost))
    inventory_entry_cut = int(round(position_ratio * config.inventory_entry_size_cut))

    if position > 0:
        ask_size += inventory_exit_boost
        bid_size -= inventory_entry_cut
    elif position < 0:
        bid_size += inventory_exit_boost
        ask_size -= inventory_entry_cut

    if position_ratio >= config.reduce_only_inventory_ratio:
        if position > 0:
            bid_size = 0
            ask_size = max(ask_size, base_size + 2)
        elif position < 0:
            ask_size = 0
            bid_size = max(bid_size, base_size + 2)

    bid_size = int(clamp(bid_size, 0, config.max_passive_size))
    ask_size = int(clamp(ask_size, 0, config.max_passive_size))
    return bid_size, ask_size


def suppress_side(
    side: str,
    position: int,
    position_limit: int,
    deviation: float,
    config: OsmiumConfig,
) -> bool:
    if position_limit <= 0:
        return True

    position_ratio = abs(position) / position_limit

    # Limit protection: near hard limits we mostly only trade to reduce.
    if position_ratio >= config.reduce_only_inventory_ratio:
        if side == "buy" and position > 0:
            return True
        if side == "sell" and position < 0:
            return True

    # Strong deviation plus same-direction inventory suppresses the unfavored
    # quote to preserve scarce capacity.
    if side == "buy" and deviation > config.quote_suppression_deviation and position > 0:
        return position_ratio >= config.quote_suppression_inventory_ratio
    if side == "sell" and deviation < -config.quote_suppression_deviation and position < 0:
        return position_ratio >= config.quote_suppression_inventory_ratio

    return False


def aggressive_take_threshold(
    side: str,
    favored_side: str,
    projected_position: int,
    position_limit: int,
    abs_deviation: float,
    config: OsmiumConfig,
) -> float:
    threshold = config.aggressive_take_threshold
    position_ratio = abs(projected_position) / position_limit if position_limit > 0 else 1.0
    side_sign = 1 if side == "buy" else -1

    if favored_side == side:
        threshold -= config.aggressive_favored_side_relief
        if abs_deviation >= config.stretched_deviation_band:
            threshold -= config.stretched_take_threshold_relief

    # Adding in the same inventory direction becomes progressively stricter.
    if projected_position * side_sign > 0:
        threshold += position_ratio * config.aggressive_same_direction_penalty
    elif projected_position * side_sign < 0:
        threshold -= min(config.aggressive_favored_side_relief, position_ratio * config.aggressive_favored_side_relief)

    return max(2.0, threshold)


def take_orders(
    order_depth: Any,
    adjusted_fair: float,
    favored_side: str,
    starting_position: int,
    position_limit: int,
    abs_deviation: float,
    config: OsmiumConfig,
) -> tuple[List[Order], int, Dict[str, Any]]:
    orders: List[Order] = []
    diagnostics = {
        "aggressive_buy_qty": 0,
        "aggressive_sell_qty": 0,
        "aggressive_buy_threshold": None,
        "aggressive_sell_threshold": None,
    }
    projected_position = starting_position
    remaining_total_take = config.max_total_aggressive_take

    sell_orders = normalize_book_side(order_depth, "sell_orders")
    for ask_price in sorted(sell_orders):
        if remaining_total_take <= 0:
            break

        threshold = aggressive_take_threshold(
            "buy",
            favored_side,
            projected_position,
            position_limit,
            abs_deviation,
            config,
        )
        diagnostics["aggressive_buy_threshold"] = round(threshold, 4)
        edge = adjusted_fair - ask_price

        position_ratio = abs(projected_position) / position_limit if position_limit > 0 else 1.0
        same_direction = projected_position > 0
        if same_direction and position_ratio >= config.hard_limit_inventory_ratio and edge < config.aggressive_extreme_edge_threshold:
            break
        if edge < threshold:
            break

        take_size = min(
            abs(int(sell_orders[ask_price])),
            config.max_aggressive_take_size,
            remaining_total_take,
            buy_capacity(projected_position, position_limit),
        )
        if take_size <= 0:
            break

        orders.append(Order(PRODUCT, int(ask_price), int(take_size)))
        projected_position += take_size
        remaining_total_take -= take_size
        diagnostics["aggressive_buy_qty"] += take_size

    buy_orders = normalize_book_side(order_depth, "buy_orders")
    for bid_price in sorted(buy_orders, reverse=True):
        if remaining_total_take <= 0:
            break

        threshold = aggressive_take_threshold(
            "sell",
            favored_side,
            projected_position,
            position_limit,
            abs_deviation,
            config,
        )
        diagnostics["aggressive_sell_threshold"] = round(threshold, 4)
        edge = bid_price - adjusted_fair

        position_ratio = abs(projected_position) / position_limit if position_limit > 0 else 1.0
        same_direction = projected_position < 0
        if same_direction and position_ratio >= config.hard_limit_inventory_ratio and edge < config.aggressive_extreme_edge_threshold:
            break
        if edge < threshold:
            break

        take_size = min(
            abs(int(buy_orders[bid_price])),
            config.max_aggressive_take_size,
            remaining_total_take,
            sell_capacity(projected_position, position_limit),
        )
        if take_size <= 0:
            break

        orders.append(Order(PRODUCT, int(bid_price), -int(take_size)))
        projected_position -= take_size
        remaining_total_take -= take_size
        diagnostics["aggressive_sell_qty"] += take_size

    return orders, projected_position, diagnostics


def passive_quotes(
    ctx: MarketContext,
    adjusted_fair: float,
    favored_side: str,
    projected_position: int,
    config: OsmiumConfig,
) -> tuple[int | None, int | None, int, int]:
    deviation = ctx.mid_price - config.fair_value
    abs_deviation = abs(deviation)
    position_ratio = abs(projected_position) / ctx.position_limit if ctx.position_limit > 0 else 1.0

    common_spread = common_half_spread(abs_deviation, config)
    bid_side_adjustment, ask_side_adjustment = side_offsets(
        favored_side=favored_side,
        deviation=deviation,
        position_ratio=position_ratio,
        position=projected_position,
        config=config,
    )

    bid_offset = max(1.0, common_spread + bid_side_adjustment)
    ask_offset = max(1.0, common_spread + ask_side_adjustment)

    # Passive quoting is centered on adjusted reservation price, not raw fair.
    bid_quote = math.floor(adjusted_fair - bid_offset)
    ask_quote = math.ceil(adjusted_fair + ask_offset)

    # When deviation is large enough to matter, the favored side may improve the
    # current best quote by 1 tick. This only affects the favored side; the
    # unwanted side is handled via wider offsets above.
    if (
        abs_deviation > config.moderate_deviation_band
        and ctx.best_bid_price is not None
        and ctx.best_ask_price is not None
        and ctx.best_bid_price < ctx.best_ask_price
    ):
        if favored_side == "buy":
            bid_quote = ctx.best_bid_price + 1
        elif favored_side == "sell":
            ask_quote = ctx.best_ask_price - 1

    # Re-apply fair-value and no-crossing guardrails after any queue-priority
    # adjustment so the favored side never violates fair or crosses the spread.
    bid_quote = min(bid_quote, config.fair_value - config.fair_value_quote_buffer)
    ask_quote = max(ask_quote, config.fair_value + config.fair_value_quote_buffer)
    if ctx.best_ask_price is not None:
        bid_quote = min(bid_quote, ctx.best_ask_price - 1)
    if ctx.best_bid_price is not None:
        ask_quote = max(ask_quote, ctx.best_bid_price + 1)

    bid_size, ask_size = passive_sizes(
        favored_side=favored_side,
        abs_deviation=abs_deviation,
        position=projected_position,
        position_limit=ctx.position_limit,
        config=config,
    )

    if suppress_side("buy", projected_position, ctx.position_limit, deviation, config):
        bid_quote = None
        bid_size = 0
    if suppress_side("sell", projected_position, ctx.position_limit, deviation, config):
        ask_quote = None
        ask_size = 0

    bid_size = min(bid_size, buy_capacity(projected_position, ctx.position_limit))
    ask_size = min(ask_size, sell_capacity(projected_position, ctx.position_limit))

    if bid_quote is not None and ask_quote is not None and bid_quote >= ask_quote:
        if projected_position > 0 or favored_side == "sell":
            bid_quote = None
            bid_size = 0
        elif projected_position < 0 or favored_side == "buy":
            ask_quote = None
            ask_size = 0
        else:
            bid_quote = None
            ask_quote = None
            bid_size = 0
            ask_size = 0

    if bid_size <= 0:
        bid_quote = None
    if ask_size <= 0:
        ask_quote = None

    return bid_quote, ask_quote, bid_size, ask_size


def build_orders(ctx: MarketContext, order_depth: Any, config: OsmiumConfig) -> StrategyResult:
    adjusted_fair, price_component, inventory_component = reservation_price(ctx, config)
    deviation = ctx.mid_price - config.fair_value
    abs_deviation = abs(deviation)
    favored_side = favored_side_from_deviation(deviation)

    aggressive_orders, projected_position, aggressive_diagnostics = take_orders(
        order_depth=order_depth,
        adjusted_fair=adjusted_fair,
        favored_side=favored_side,
        starting_position=ctx.position,
        position_limit=ctx.position_limit,
        abs_deviation=abs_deviation,
        config=config,
    )

    bid_quote, ask_quote, bid_size, ask_size = passive_quotes(
        ctx=ctx,
        adjusted_fair=adjusted_fair,
        favored_side=favored_side,
        projected_position=projected_position,
        config=config,
    )

    orders = list(aggressive_orders)
    if bid_quote is not None and bid_size > 0:
        orders.append(Order(PRODUCT, int(bid_quote), int(bid_size)))
    if ask_quote is not None and ask_size > 0:
        orders.append(Order(PRODUCT, int(ask_quote), -int(ask_size)))

    return StrategyResult(
        orders=orders,
        diagnostics={
            "fair_value": config.fair_value,
            "mid_price": round(ctx.mid_price, 4),
            "deviation_from_fair": round(deviation, 4),
            "favored_side": favored_side,
            "position": ctx.position,
            "projected_position_after_taking": projected_position,
            "position_limit": ctx.position_limit,
            "position_ratio": round(abs(ctx.position) / ctx.position_limit, 4) if ctx.position_limit > 0 else None,
            "adjusted_fair": round(adjusted_fair, 4),
            "reservation_price_deviation_component": round(price_component, 4),
            "reservation_price_inventory_component": round(inventory_component, 4),
            "passive_bid_quote": bid_quote,
            "passive_ask_quote": ask_quote,
            "passive_bid_size": bid_size,
            "passive_ask_size": ask_size,
            **aggressive_diagnostics,
        },
    )


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


class Trader:
    def __init__(
        self,
        config: OsmiumConfig | None = None,
        config_overrides: Mapping[str, Any] | None = None,
    ) -> None:
        if config is not None and config_overrides is not None:
            raise ValueError("Pass either config or config_overrides, not both")

        if config is not None:
            self.config = config
            return

        self.config = build_osmium_config(dict(config_overrides) if config_overrides else None)

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {PRODUCT: []}

        position_limit = POSITION_LIMITS.get(PRODUCT, DEFAULT_POSITION_LIMIT)
        ctx = build_context(state, PRODUCT, position_limit, self.config)
        if ctx is None:
            return result, 0, dump_payload({"version": 1, PRODUCT: {"status": "no_book"}})

        order_depths = safe_getattr_or_key(state, "order_depths", {})
        order_depth = order_depths.get(PRODUCT) if isinstance(order_depths, Mapping) else None
        strategy_result = build_orders(ctx, order_depth, self.config)
        result[PRODUCT] = strategy_result.orders

        payload = load_payload(read_trader_data(state))
        payload["version"] = 1

        previous_payload = payload
        product_state = previous_payload.get(PRODUCT, {})
        if not isinstance(product_state, dict):
            product_state = {}

        product_state.update(strategy_result.diagnostics)
        payload[PRODUCT] = product_state
        trader_data = dump_payload(payload)
        return result, 0, trader_data
