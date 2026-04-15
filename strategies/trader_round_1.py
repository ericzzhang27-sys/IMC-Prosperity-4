from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, List

# Import trading data models, fallback to local if not available
try:
    from datamodel import Order, TradingState
except ImportError:
    from local_datamodel import Order, TradingState

# Constants for the trading product and default settings
INTARIAN_PEPPER_ROOT = "INTARIAN_PEPPER_ROOT"
DEFAULT_POSITION_LIMIT = 80
DEFAULT_STRATEGY_NAME = "buy_and_hold"

# Position limits per product
POSITION_LIMITS = {
    INTARIAN_PEPPER_ROOT: 80,
}


# Data class holding the current market context for a product
@dataclass(frozen=True)
class StrategyContext:
    product: str
    timestamp: int
    position: int
    position_limit: int
    best_bid_price: int | None
    best_bid_volume: int | None
    best_ask_price: int | None
    best_ask_volume: int | None
    mid_price: float | None
    trader_state: Dict[str, Any]


# Result of building orders for a strategy
@dataclass
class StrategyResult:
    orders: List[Order] = field(default_factory=list)
    state_update: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)


# Features extracted for signal detection
@dataclass(frozen=True)
class SignalFeatures:
    trend_fair_value: float | None
    residual: float | None
    residual_change: float | None
    signal_strength_ticks: float
    proposed_adjustment_ticks: float
    spread_ticks: int | None
    imbalance: float | None
    volatility_ema_ticks: float | None


# State of the signal management
@dataclass(frozen=True)
class ManagedSignalState:
    active: bool
    adjustment_ticks: float
    strength_ticks: float
    steps_remaining: int
    activation_residual: float | None
    activation_timestamp: int | None
    reason: str


# Base strategy class
class PepperStrategy:
    name = "base"

    def build_orders(self, ctx: StrategyContext) -> StrategyResult:
        raise NotImplementedError


# Configuration for the signal-aware passive market maker strategy
@dataclass(frozen=True)
class SignalAwarePassiveMarketMakerConfig:
    # Keep this below the exchange limit if you want a softer internal cap.
    internal_position_limit: int | None = None

    # Trend-driven inventory: keep a mild persistent long bias and only add a
    # smaller temporary overlay when a pullback signal is active.
    trend_target_inventory_ratio: float = 0.6
    signal_extra_inventory_ratio: float = 0.2

    # Online trend model. We quote around a predicted trending fair value, not
    # around the instantaneous mid.
    initial_trend_slope_per_timestamp: float = 0.001
    trend_level_alpha: float = 0.10
    trend_slope_alpha: float = 0.03
    max_abs_trend_slope_per_timestamp: float = 0.01

    # Quote construction.
    quote_improvement_ticks: int = 1
    base_half_spread_ticks: float = 6.0
    inventory_reservation_skew_per_unit: float = 0.05
    max_inventory_reservation_shift_ticks: float = 4.0
    imbalance_reservation_skew_ticks: float = 1.5
    max_imbalance_shift_ticks: float = 1.5

    # Residual-based pullback signal. Residual level captures how far below
    # trend we are; residual change captures the fresh downward shock.
    signal_residual_weight: float = 0.8
    signal_residual_change_weight: float = 1.2
    max_signal_adjustment_ticks: float = 4.0

    # Signal quote skew: make bids more aggressive and asks less aggressive.
    signal_bid_extra_aggression_ticks: int = 3
    signal_ask_retreat_ticks: int = 1
    extra_bid_aggression_when_below_target: int = 3
    extra_ask_retreat_when_below_target: int = 0
    extra_bid_conservatism_when_above_target: int = 1
    extra_ask_aggression_when_above_target: int = 1

    # Baseline size and asymmetric size under signal.
    base_bid_size: int = 10
    base_ask_size: int = 10
    max_bid_size: int = 18
    max_ask_size: int = 18
    min_bid_size: int = 1
    min_ask_size: int = 1
    inventory_rebalance_step: int = 10
    signal_bid_size_boost: int = 3
    signal_ask_size_cut: int = 2

    # Optional signal filters.
    enable_min_signal_strength_filter: bool = True
    min_signal_strength_ticks: float = 1.25
    enable_max_spread_filter: bool = True
    max_signal_spread_ticks: int = 18
    enable_volatility_filter: bool = True
    volatility_ema_alpha: float = 0.15
    max_signal_volatility_ema_ticks: float = 2.5
    enable_imbalance_filter: bool = False
    min_signal_imbalance: float = 0.0
    enable_edge_filter: bool = True
    min_signal_adjustment_ticks: float = 1.0

    # Simple, residual-based signal monetization and decay. The signal turns
    # off when the pullback has mostly reverted back to trend, or when the
    # residual keeps moving the wrong way for too long.
    signal_holding_steps: int = 5
    signal_decay_per_step: float = 0.75
    signal_residual_exit_threshold_ticks: float = -0.25
    signal_adverse_residual_ticks: float = 5.5


# Strategy configurations per product
PRODUCT_STRATEGY_CONFIGS = {
    INTARIAN_PEPPER_ROOT: SignalAwarePassiveMarketMakerConfig(
        internal_position_limit=None,
        trend_target_inventory_ratio=0.60,
        signal_extra_inventory_ratio=0.2,
        initial_trend_slope_per_timestamp=0.001,
        trend_level_alpha=0.10,
        trend_slope_alpha=0.03,
        max_abs_trend_slope_per_timestamp=0.01,
        quote_improvement_ticks=2,
        base_half_spread_ticks=4.5,
        inventory_reservation_skew_per_unit=0.05,
        max_inventory_reservation_shift_ticks=4.0,
        imbalance_reservation_skew_ticks=1.5,
        max_imbalance_shift_ticks=1.5,
        signal_residual_weight=0.8,
        signal_residual_change_weight=1.2,
        max_signal_adjustment_ticks=4.0,
        signal_bid_extra_aggression_ticks=3,
        signal_ask_retreat_ticks=1,
        extra_bid_aggression_when_below_target=3,
        extra_ask_retreat_when_below_target=0,
        extra_bid_conservatism_when_above_target=1,
        extra_ask_aggression_when_above_target=1,
        base_bid_size=12,
        base_ask_size=8,
        max_bid_size=22,
        max_ask_size=16,
        min_bid_size=1,
        min_ask_size=1,
        inventory_rebalance_step=8,
        signal_bid_size_boost=5,
        signal_ask_size_cut=1,
        enable_min_signal_strength_filter=True,
        min_signal_strength_ticks=1.25,
        enable_max_spread_filter=True,
        max_signal_spread_ticks=18,
        enable_volatility_filter=True,
        volatility_ema_alpha=0.15,
        max_signal_volatility_ema_ticks=2.5,
        enable_imbalance_filter=False,
        min_signal_imbalance=0.0,
        enable_edge_filter=True,
        min_signal_adjustment_ticks=1.0,
        signal_holding_steps=5,
        signal_decay_per_step=0.75,
        signal_residual_exit_threshold_ticks=-0.25,
        signal_adverse_residual_ticks=5.5,
    )
}


# Utility functions for safe attribute/key access and type coercion
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


def coerce_float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


# Functions for reading and serializing trader state
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


def make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(key): make_json_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_json_safe(inner) for inner in value]
    return str(value)


def dump_payload(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(make_json_safe(payload), separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return "{}"


# Functions for reading market data
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


# Get the best bid from order depth
def best_bid(order_depth: Any) -> tuple[int, int] | None:
    buy_orders = normalize_book_side(order_depth, "buy_orders")
    if not buy_orders:
        return None
    price = max(buy_orders)
    return price, buy_orders[price]


# Get the best ask from order depth
def best_ask(order_depth: Any) -> tuple[int, int] | None:
    sell_orders = normalize_book_side(order_depth, "sell_orders")
    if not sell_orders:
        return None
    price = min(sell_orders)
    return price, sell_orders[price]


# Calculate midpoint price
def midpoint(best_bid_price: int | None, best_ask_price: int | None) -> float | None:
    if best_bid_price is None or best_ask_price is None:
        return None
    return (best_bid_price + best_ask_price) / 2.0


# Calculate remaining buy capacity
def buy_capacity(position: int, position_limit: int) -> int:
    return max(0, position_limit - position)


# Calculate remaining sell capacity
def sell_capacity(position: int, position_limit: int) -> int:
    return max(0, position + position_limit)


# Clamp ratio between -1 and 1
def clamp_ratio(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


# Get effective position limit
def effective_strategy_position_limit(exchange_limit: int, internal_limit: int | None) -> int:
    hard_limit = max(0, int(exchange_limit))
    if internal_limit is None:
        return hard_limit
    return max(0, min(hard_limit, int(internal_limit)))


# Calculate target inventory from ratio
def target_inventory_from_ratio(strategy_limit: int, target_inventory_ratio: float) -> int:
    ratio = clamp_ratio(target_inventory_ratio)
    return max(-strategy_limit, min(strategy_limit, int(round(strategy_limit * ratio))))


# Get visible size
def visible_size(volume: int | None) -> int | None:
    if volume is None:
        return None
    return abs(int(volume))


# Compute order book imbalance
def compute_book_imbalance(best_bid_volume: int | None, best_ask_volume: int | None) -> float | None:
    bid_visible = visible_size(best_bid_volume)
    ask_visible = visible_size(best_ask_volume)
    if bid_visible is None or ask_visible is None:
        return None
    total = bid_visible + ask_visible
    if total <= 0:
        return None
    return (bid_visible - ask_visible) / total


# Load previous signal state from trader state
def load_previous_signal_state(trader_state: Mapping[str, Any]) -> ManagedSignalState:
    return ManagedSignalState(
        active=bool(trader_state.get("signal_active", False)),
        adjustment_ticks=max(0.0, coerce_float_or_none(trader_state.get("signal_adjustment_ticks")) or 0.0),
        strength_ticks=max(0.0, coerce_float_or_none(trader_state.get("signal_strength_ticks")) or 0.0),
        steps_remaining=max(0, coerce_int_or_none(trader_state.get("signal_steps_remaining")) or 0),
        activation_residual=coerce_float_or_none(trader_state.get("signal_activation_residual")),
        activation_timestamp=coerce_int_or_none(trader_state.get("signal_activation_timestamp")),
        reason=str(trader_state.get("signal_reason", "inactive")),
    )


# Compute trend fair value and slope
# Compute trend fair value and slope
def compute_trend_fair_value(
    ctx: StrategyContext,
    config: SignalAwarePassiveMarketMakerConfig,
) -> tuple[float | None, float]:
    if ctx.mid_price is None:
        return None, config.initial_trend_slope_per_timestamp

    previous_trend_value = coerce_float_or_none(ctx.trader_state.get("trend_value"))
    previous_slope = coerce_float_or_none(ctx.trader_state.get("trend_slope_per_timestamp"))
    previous_timestamp = coerce_int_or_none(ctx.trader_state.get("trend_timestamp"))

    trend_slope = previous_slope
    if trend_slope is None:
        trend_slope = config.initial_trend_slope_per_timestamp

    trend_slope = max(
        -config.max_abs_trend_slope_per_timestamp,
        min(config.max_abs_trend_slope_per_timestamp, trend_slope),
    )

    if previous_trend_value is None or previous_timestamp is None:
        return ctx.mid_price, trend_slope

    dt = max(0, ctx.timestamp - previous_timestamp)
    return previous_trend_value + (trend_slope * dt), trend_slope


# Update trend state based on new observations
def update_trend_state(
    ctx: StrategyContext,
    trend_fair_value: float | None,
    trend_slope: float,
    config: SignalAwarePassiveMarketMakerConfig,
) -> Dict[str, Any]:
    if ctx.mid_price is None or trend_fair_value is None:
        return {}

    previous_mid = coerce_float_or_none(ctx.trader_state.get("last_mid"))
    previous_timestamp = coerce_int_or_none(ctx.trader_state.get("last_timestamp"))
    dt = max(1, ctx.timestamp - previous_timestamp) if previous_timestamp is not None else 1

    level_alpha = max(0.0, min(1.0, config.trend_level_alpha))
    slope_alpha = max(0.0, min(1.0, config.trend_slope_alpha))

    if previous_mid is None:
        observed_slope = trend_slope
    else:
        observed_slope = (ctx.mid_price - previous_mid) / dt

    updated_slope = trend_slope + (slope_alpha * (observed_slope - trend_slope))
    updated_slope = max(
        -config.max_abs_trend_slope_per_timestamp,
        min(config.max_abs_trend_slope_per_timestamp, updated_slope),
    )
    updated_trend_value = trend_fair_value + (level_alpha * (ctx.mid_price - trend_fair_value))

    return {
        "trend_value": round(updated_trend_value, 4),
        "trend_slope_per_timestamp": round(updated_slope, 8),
        "trend_timestamp": ctx.timestamp,
    }


def compute_signal_features(
    ctx: StrategyContext,
    trend_fair_value: float | None,
    config: SignalAwarePassiveMarketMakerConfig,
) -> SignalFeatures:
    residual = None
    if ctx.mid_price is not None and trend_fair_value is not None:
        residual = ctx.mid_price - trend_fair_value

    previous_residual = coerce_float_or_none(ctx.trader_state.get("last_residual"))
    residual_change = None
    if residual is not None and previous_residual is not None:
        residual_change = residual - previous_residual

    negative_residual_ticks = max(0.0, -(residual or 0.0))
    negative_residual_change_ticks = 0.0
    if residual is not None and residual <= 0.0:
        negative_residual_change_ticks = max(0.0, -(residual_change or 0.0))

    signal_strength_ticks = (
        (config.signal_residual_weight * negative_residual_ticks)
        + (config.signal_residual_change_weight * negative_residual_change_ticks)
    )
    proposed_adjustment_ticks = min(config.max_signal_adjustment_ticks, signal_strength_ticks)

    previous_volatility_ema = coerce_float_or_none(ctx.trader_state.get("volatility_ema_ticks"))
    observed_volatility = abs(residual_change) if residual_change is not None else None
    if observed_volatility is None:
        previous_mid = coerce_float_or_none(ctx.trader_state.get("last_mid"))
        if ctx.mid_price is not None and previous_mid is not None:
            observed_volatility = abs(ctx.mid_price - previous_mid)

    if observed_volatility is None:
        volatility_ema_ticks = previous_volatility_ema
    elif previous_volatility_ema is None:
        volatility_ema_ticks = observed_volatility
    else:
        alpha = max(0.0, min(1.0, config.volatility_ema_alpha))
        volatility_ema_ticks = (alpha * observed_volatility) + ((1.0 - alpha) * previous_volatility_ema)

    spread_ticks = None
    if ctx.best_bid_price is not None and ctx.best_ask_price is not None:
        spread_ticks = max(0, int(ctx.best_ask_price - ctx.best_bid_price))

    return SignalFeatures(
        trend_fair_value=trend_fair_value,
        residual=residual,
        residual_change=residual_change,
        signal_strength_ticks=signal_strength_ticks,
        proposed_adjustment_ticks=proposed_adjustment_ticks,
        spread_ticks=spread_ticks,
        imbalance=compute_book_imbalance(ctx.best_bid_volume, ctx.best_ask_volume),
        volatility_ema_ticks=volatility_ema_ticks,
    )


def signal_filters_pass(
    features: SignalFeatures,
    config: SignalAwarePassiveMarketMakerConfig,
) -> tuple[bool, str]:
    if features.residual is None or features.signal_strength_ticks <= 0.0:
        return False, "no_negative_residual_signal"
    if config.enable_min_signal_strength_filter and features.signal_strength_ticks < config.min_signal_strength_ticks:
        return False, "signal_too_weak"
    if (
        config.enable_max_spread_filter
        and features.spread_ticks is not None
        and features.spread_ticks > config.max_signal_spread_ticks
    ):
        return False, "spread_too_wide"
    if (
        config.enable_volatility_filter
        and features.volatility_ema_ticks is not None
        and features.volatility_ema_ticks > config.max_signal_volatility_ema_ticks
    ):
        return False, "volatility_too_high"
    if (
        config.enable_imbalance_filter
        and features.imbalance is not None
        and features.imbalance < config.min_signal_imbalance
    ):
        return False, "imbalance_not_supportive"
    if config.enable_edge_filter and features.proposed_adjustment_ticks < config.min_signal_adjustment_ticks:
        return False, "edge_below_cost_proxy"
    return True, "filters_passed"


def evolve_signal_state(
    ctx: StrategyContext,
    features: SignalFeatures,
    config: SignalAwarePassiveMarketMakerConfig,
) -> tuple[ManagedSignalState, str]:
    previous_state = load_previous_signal_state(ctx.trader_state)
    filter_passed, filter_reason = signal_filters_pass(features, config)

    active = previous_state.active
    adjustment_ticks = previous_state.adjustment_ticks
    strength_ticks = previous_state.strength_ticks
    steps_remaining = previous_state.steps_remaining
    activation_residual = previous_state.activation_residual
    activation_timestamp = previous_state.activation_timestamp
    reason = previous_state.reason

    if active:
        if (
            features.residual is not None
            and features.residual >= config.signal_residual_exit_threshold_ticks
        ):
            active = False
            reason = "residual_reverted"
        elif (
            config.signal_adverse_residual_ticks > 0
            and features.residual is not None
            and features.residual <= -config.signal_adverse_residual_ticks
        ):
            active = False
            reason = "residual_stop"
        else:
            steps_remaining = max(0, steps_remaining - 1)
            adjustment_ticks *= config.signal_decay_per_step
            strength_ticks *= config.signal_decay_per_step
            if steps_remaining <= 0 or adjustment_ticks < 0.05:
                active = False
                reason = "signal_decay"
            else:
                reason = "signal_carry"

    if filter_passed and (not active or features.proposed_adjustment_ticks >= adjustment_ticks):
        active = True
        adjustment_ticks = features.proposed_adjustment_ticks
        strength_ticks = features.signal_strength_ticks
        steps_remaining = max(1, int(config.signal_holding_steps))
        activation_residual = features.residual
        activation_timestamp = ctx.timestamp
        reason = "negative_residual_pullback"

    if not active:
        adjustment_ticks = 0.0
        strength_ticks = 0.0
        steps_remaining = 0
        activation_residual = None
        if reason == previous_state.reason:
            reason = filter_reason

    return (
        ManagedSignalState(
            active=active,
            adjustment_ticks=adjustment_ticks,
            strength_ticks=strength_ticks,
            steps_remaining=steps_remaining,
            activation_residual=activation_residual,
            activation_timestamp=activation_timestamp,
            reason=reason,
        ),
        filter_reason,
    )


def compute_reservation_values(
    trend_fair_value: float | None,
    residual: float | None,
    position: int,
    target_inventory: int,
    imbalance: float | None,
    config: SignalAwarePassiveMarketMakerConfig,
) -> tuple[float | None, float | None, float, float]:
    if trend_fair_value is None:
        return None, None, 0.0, 0.0

    negative_residual_ticks = max(0.0, -(residual or 0.0))
    residual_shift = min(
        config.max_signal_adjustment_ticks,
        config.signal_residual_weight * negative_residual_ticks,
    )

    imbalance_shift = 0.0
    if imbalance is not None:
        imbalance_shift = config.imbalance_reservation_skew_ticks * imbalance
        imbalance_shift = max(
            -config.max_imbalance_shift_ticks,
            min(config.max_imbalance_shift_ticks, imbalance_shift),
        )

    inventory_gap = position - target_inventory
    raw_inventory_shift = config.inventory_reservation_skew_per_unit * inventory_gap
    inventory_shift = max(
        -config.max_inventory_reservation_shift_ticks,
        min(config.max_inventory_reservation_shift_ticks, raw_inventory_shift),
    )

    reservation_price = trend_fair_value + residual_shift + imbalance_shift - inventory_shift
    fair_value = trend_fair_value + residual_shift + imbalance_shift
    return fair_value, reservation_price, inventory_shift, imbalance_shift


def compute_quote_half_spread_ticks(
    spread_ticks: int | None,
    config: SignalAwarePassiveMarketMakerConfig,
) -> float:
    if spread_ticks is None:
        return max(1.0, config.base_half_spread_ticks)
    inside_half_spread = max(1.0, (spread_ticks / 2.0) - config.quote_improvement_ticks)
    return max(1.0, min(config.base_half_spread_ticks, inside_half_spread))


def compute_quote_skew_ticks(
    position: int,
    target_inventory: int,
    signal_active: bool,
    config: SignalAwarePassiveMarketMakerConfig,
) -> tuple[int, int]:
    if not signal_active:
        return 0, 0

    bid_extra = int(config.signal_bid_extra_aggression_ticks)
    ask_retreat = int(config.signal_ask_retreat_ticks)

    if position < target_inventory:
        bid_extra += int(config.extra_bid_aggression_when_below_target)
        ask_retreat += int(config.extra_ask_retreat_when_below_target)
    elif position > target_inventory:
        bid_extra -= int(config.extra_bid_conservatism_when_above_target)
        ask_retreat += int(config.extra_ask_aggression_when_above_target)

    return max(0, bid_extra), max(0, ask_retreat)


def compute_passive_quotes(
    ctx: StrategyContext,
    reservation_price: float | None,
    target_inventory: int,
    signal_active: bool,
    spread_ticks: int | None,
    config: SignalAwarePassiveMarketMakerConfig,
) -> tuple[int | None, int | None]:
    if (
        ctx.best_bid_price is None
        or ctx.best_ask_price is None
        or reservation_price is None
        or ctx.best_bid_price >= ctx.best_ask_price
    ):
        return None, None

    half_spread_ticks = compute_quote_half_spread_ticks(spread_ticks, config)
    raw_bid = math.floor(reservation_price - half_spread_ticks)
    raw_ask = math.ceil(reservation_price + half_spread_ticks)

    bid_inside = ctx.best_bid_price
    ask_inside = ctx.best_ask_price
    if ctx.best_bid_price + config.quote_improvement_ticks < ctx.best_ask_price:
        bid_inside = ctx.best_bid_price + config.quote_improvement_ticks
    if ctx.best_ask_price - config.quote_improvement_ticks > ctx.best_bid_price:
        ask_inside = ctx.best_ask_price - config.quote_improvement_ticks

    bid_extra, ask_retreat = compute_quote_skew_ticks(
        position=ctx.position,
        target_inventory=target_inventory,
        signal_active=signal_active,
        config=config,
    )

    bid_anchor = min(ctx.best_ask_price - 1, bid_inside + bid_extra)
    ask_anchor = max(ctx.best_bid_price + 1, ask_inside + ask_retreat, ctx.best_ask_price)

    bid_quote = min(ctx.best_ask_price - 1, max(raw_bid, bid_anchor))
    ask_quote = max(ctx.best_bid_price + 1, max(raw_ask, ask_anchor))

    if bid_quote >= ask_quote:
        if ctx.position < target_inventory:
            ask_quote = None
        elif ctx.position > target_inventory:
            bid_quote = None
        else:
            bid_quote = None
            ask_quote = None

    return bid_quote, ask_quote


def compute_quote_sizes(
    position: int,
    strategy_limit: int,
    target_inventory: int,
    signal_active: bool,
    config: SignalAwarePassiveMarketMakerConfig,
) -> tuple[int, int]:
    inventory_gap = target_inventory - position
    rebalance_step = max(1, int(config.inventory_rebalance_step))
    rebalance_units = int(math.ceil(abs(inventory_gap) / rebalance_step)) if inventory_gap != 0 else 0

    bid_size = int(config.base_bid_size)
    ask_size = int(config.base_ask_size)

    if inventory_gap > 0:
        bid_size += rebalance_units
        ask_size -= rebalance_units
    elif inventory_gap < 0:
        ask_size += rebalance_units
        bid_size -= rebalance_units

    if signal_active:
        bid_size += int(config.signal_bid_size_boost)
        ask_size -= int(config.signal_ask_size_cut)

    if signal_active and position >= target_inventory:
        bid_size -= int(config.signal_bid_size_boost)
        ask_size += int(config.signal_ask_size_cut)

    bid_size = max(int(config.min_bid_size), min(int(config.max_bid_size), bid_size))
    ask_size = max(int(config.min_ask_size), min(int(config.max_ask_size), ask_size))

    bid_size = min(bid_size, buy_capacity(position, strategy_limit))
    ask_size = min(ask_size, sell_capacity(position, strategy_limit))

    if position >= strategy_limit:
        bid_size = 0
    if position <= -strategy_limit:
        ask_size = 0

    return bid_size, ask_size


# Buy-and-hold strategy: buy the best available ask until the position limit is reached.
class BuyAndHoldStrategy(PepperStrategy):
    name = "buy_and_hold"

    def build_orders(self, ctx: StrategyContext) -> StrategyResult:
        buy_quantity = buy_capacity(ctx.position, ctx.position_limit)

        orders: List[Order] = []
        if ctx.best_ask_price is not None and buy_quantity > 0:
            orders.append(Order(ctx.product, int(ctx.best_ask_price), int(buy_quantity)))

        return StrategyResult(
            orders=orders,
            state_update={
                "strategy_position_limit": ctx.position_limit,
                "target_inventory": ctx.position_limit,
                "best_ask_price": ctx.best_ask_price,
                "buy_quantity": buy_quantity,
            },
            diagnostics={
                "strategy": self.name,
                "position": ctx.position,
                "mid_price": ctx.mid_price,
                "best_ask_price": ctx.best_ask_price,
                "buy_quantity": buy_quantity,
            },
        )


STRATEGY_REGISTRY: Dict[str, PepperStrategy] = {
    BuyAndHoldStrategy.name: BuyAndHoldStrategy(),
}


def choose_active_strategy(product_state: Mapping[str, Any]) -> PepperStrategy:
    strategy_name = str(product_state.get("active_strategy", DEFAULT_STRATEGY_NAME))
    return STRATEGY_REGISTRY.get(strategy_name, STRATEGY_REGISTRY[DEFAULT_STRATEGY_NAME])


def build_context(
    product: str,
    order_depth: Any,
    timestamp: int,
    position: int,
    position_limit: int,
    trader_state: Mapping[str, Any],
) -> StrategyContext:
    top_bid = best_bid(order_depth)
    top_ask = best_ask(order_depth)

    return StrategyContext(
        product=product,
        timestamp=timestamp,
        position=position,
        position_limit=position_limit,
        best_bid_price=top_bid[0] if top_bid is not None else None,
        best_bid_volume=top_bid[1] if top_bid is not None else None,
        best_ask_price=top_ask[0] if top_ask is not None else None,
        best_ask_volume=top_ask[1] if top_ask is not None else None,
        mid_price=midpoint(
            top_bid[0] if top_bid is not None else None,
            top_ask[0] if top_ask is not None else None,
        ),
        trader_state=dict(trader_state),
    )


# Main trader class that runs the strategy
class Trader:
    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {}
        positions = read_positions(state)
        order_depths = read_order_depths(state)

        persisted = load_payload(read_trader_data(state))
        round_state = persisted.get("round_1", {})
        if not isinstance(round_state, dict):
            round_state = {}

        product_state = round_state.get(INTARIAN_PEPPER_ROOT, {})
        if not isinstance(product_state, dict):
            product_state = {}

        order_depth = order_depths.get(INTARIAN_PEPPER_ROOT)
        if order_depth is None:
            result[INTARIAN_PEPPER_ROOT] = []
            next_state = dict(product_state)
        else:
            ctx = build_context(
                product=INTARIAN_PEPPER_ROOT,
                order_depth=order_depth,
                timestamp=coerce_int_or_none(safe_getattr_or_key(state, "timestamp", 0)) or 0,
                position=positions.get(INTARIAN_PEPPER_ROOT, 0),
                position_limit=POSITION_LIMITS.get(INTARIAN_PEPPER_ROOT, DEFAULT_POSITION_LIMIT),
                trader_state=product_state,
            )
            strategy = choose_active_strategy(product_state)
            strategy_result = strategy.build_orders(ctx)
            result[INTARIAN_PEPPER_ROOT] = strategy_result.orders

            next_state = dict(product_state)
            next_state.update(strategy_result.state_update)
            next_state["active_strategy"] = strategy.name
            if ctx.mid_price is not None:
                next_state["last_mid"] = round(ctx.mid_price, 4)
            next_state["last_timestamp"] = ctx.timestamp
            if strategy_result.diagnostics:
                next_state["diagnostics"] = strategy_result.diagnostics

        trader_data = dump_payload(
            {
                "version": 1,
                "round_1": {
                    INTARIAN_PEPPER_ROOT: next_state,
                },
            }
        )
        return result, 0, trader_data
