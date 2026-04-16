from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, List

try:
    from datamodel import Order, TradingState
except ImportError:
    from local_datamodel import Order, TradingState


PRODUCT = "ASH_COATED_OSMIUM"


@dataclass(frozen=True)
class SimpleOsmiumConfig:
    # Fixed anchor fair value for the stationary OSMIUM thesis.
    fair_value: int = 10000
    # Hard inventory limit used to clip passive size.
    position_limit: int = 80
    # Base fair-centered quote distance used when the book is incomplete.
    half_spread_ticks: int = 2
    # Quotes must stay at least this many ticks away from fair.
    fair_value_quote_buffer: int = 2
    # Improve by one tick only when the live spread is at least this wide.
    min_spread_to_improve_ticks: int = 5
    # Spreads at or below this threshold use the tight regime.
    tight_spread_max_ticks: int = 4
    # Spreads at or above this threshold use the wide regime.
    wide_spread_min_ticks: int = 8
    # Smaller passive size for tight spreads where queue priority is expensive.
    tight_spread_order_size: int = 6
    # Default passive size for normal spreads and missing-spread fallback.
    normal_spread_order_size: int = 10
    # Larger passive size for wide spreads with better spread-capture economics.
    wide_spread_order_size: int = 14


DEFAULT_CONFIG = SimpleOsmiumConfig()
SIMPLE_OSMIUM_CONFIG_FIELDS = tuple(asdict(DEFAULT_CONFIG).keys())


def simple_osmium_config_as_dict(config: SimpleOsmiumConfig | None = None) -> Dict[str, Any]:
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

        if isinstance(default_value, int):
            return int(value)

        if isinstance(default_value, float):
            return float(value)

        return value
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Could not coerce {name}={value!r} to {type(default_value).__name__}"
        ) from exc


def build_simple_osmium_config(overrides: Mapping[str, Any] | None = None) -> SimpleOsmiumConfig:
    if not overrides:
        return DEFAULT_CONFIG

    defaults = simple_osmium_config_as_dict(DEFAULT_CONFIG)
    coerced: Dict[str, Any] = {}
    for name, value in overrides.items():
        if name not in defaults:
            valid = ", ".join(SIMPLE_OSMIUM_CONFIG_FIELDS)
            raise ValueError(f"Unknown simple OSMIUM config field {name!r}. Valid fields: {valid}")
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


def live_spread_ticks(best_bid_price: int | None, best_ask_price: int | None) -> int | None:
    if best_bid_price is None or best_ask_price is None or best_bid_price >= best_ask_price:
        return None
    return best_ask_price - best_bid_price


def classify_spread_regime(live_spread: int | None, config: SimpleOsmiumConfig) -> str:
    if live_spread is None:
        return "normal"
    if live_spread <= config.tight_spread_max_ticks:
        return "tight"
    if live_spread >= config.wide_spread_min_ticks:
        return "wide"
    return "normal"


def spread_regime_order_size(spread_regime: str, config: SimpleOsmiumConfig) -> int:
    if spread_regime == "tight":
        return config.tight_spread_order_size
    if spread_regime == "wide":
        return config.wide_spread_order_size
    return config.normal_spread_order_size


def choose_bid_quote(
    base_bid: int,
    best_bid_price: int | None,
    best_ask_price: int | None,
    live_spread: int | None,
    config: SimpleOsmiumConfig,
) -> tuple[int | None, str, int]:
    max_bid = config.fair_value - config.fair_value_quote_buffer
    quote_mode = "base"
    pre_guardrail_bid = min(base_bid, max_bid)

    if best_bid_price is not None and live_spread is not None:
        if live_spread >= config.min_spread_to_improve_ticks:
            pre_guardrail_bid = best_bid_price + 1
            quote_mode = "improve"
        else:
            pre_guardrail_bid = best_bid_price
            quote_mode = "join"

    bid_quote = min(pre_guardrail_bid, max_bid)
    if bid_quote != pre_guardrail_bid:
        quote_mode = "clamped"

    if best_ask_price is not None and bid_quote >= best_ask_price:
        return None, "skipped", pre_guardrail_bid

    return bid_quote, quote_mode, pre_guardrail_bid


def choose_ask_quote(
    base_ask: int,
    best_bid_price: int | None,
    best_ask_price: int | None,
    live_spread: int | None,
    config: SimpleOsmiumConfig,
) -> tuple[int | None, str, int]:
    min_ask = config.fair_value + config.fair_value_quote_buffer
    quote_mode = "base"
    pre_guardrail_ask = max(base_ask, min_ask)

    if best_ask_price is not None and live_spread is not None:
        if live_spread >= config.min_spread_to_improve_ticks:
            pre_guardrail_ask = best_ask_price - 1
            quote_mode = "improve"
        else:
            pre_guardrail_ask = best_ask_price
            quote_mode = "join"

    ask_quote = max(pre_guardrail_ask, min_ask)
    if ask_quote != pre_guardrail_ask:
        quote_mode = "clamped"

    if best_bid_price is not None and ask_quote <= best_bid_price:
        return None, "skipped", pre_guardrail_ask

    return ask_quote, quote_mode, pre_guardrail_ask


class Trader:
    def __init__(
        self,
        config: SimpleOsmiumConfig | None = None,
        config_overrides: Mapping[str, Any] | None = None,
    ) -> None:
        if config is not None and config_overrides is not None:
            raise ValueError("Pass either config or config_overrides, not both")

        if config is not None:
            self.config = config
            return

        self.config = build_simple_osmium_config(dict(config_overrides) if config_overrides else None)

    def run(self, state: TradingState):
        result: Dict[str, List[Order]] = {PRODUCT: []}

        order_depths = safe_getattr_or_key(state, "order_depths", {})
        order_depth = order_depths.get(PRODUCT) if isinstance(order_depths, Mapping) else None
        if order_depth is None:
            trader_data = dump_payload({"version": 1, PRODUCT: {"status": "no_book"}})
            return result, 0, trader_data

        best_bid_level = best_bid(order_depth)
        best_ask_level = best_ask(order_depth)
        if best_bid_level is None and best_ask_level is None:
            trader_data = dump_payload({"version": 1, PRODUCT: {"status": "no_book"}})
            return result, 0, trader_data

        best_bid_price = best_bid_level[0] if best_bid_level else None
        best_ask_price = best_ask_level[0] if best_ask_level else None
        live_spread = live_spread_ticks(best_bid_price, best_ask_price)
        spread_regime = classify_spread_regime(live_spread, self.config)
        selected_order_size = spread_regime_order_size(spread_regime, self.config)

        position_map = safe_getattr_or_key(state, "position", {})
        position = 0
        if isinstance(position_map, Mapping):
            position = coerce_int_or_none(position_map.get(PRODUCT)) or 0

        base_bid = self.config.fair_value - self.config.half_spread_ticks
        base_ask = self.config.fair_value + self.config.half_spread_ticks

        bid_quote, bid_quote_mode, pre_guardrail_bid = choose_bid_quote(
            base_bid=base_bid,
            best_bid_price=best_bid_price,
            best_ask_price=best_ask_price,
            live_spread=live_spread,
            config=self.config,
        )
        ask_quote, ask_quote_mode, pre_guardrail_ask = choose_ask_quote(
            base_ask=base_ask,
            best_bid_price=best_bid_price,
            best_ask_price=best_ask_price,
            live_spread=live_spread,
            config=self.config,
        )

        buy_size = min(selected_order_size, buy_capacity(position, self.config.position_limit))
        sell_size = min(selected_order_size, sell_capacity(position, self.config.position_limit))

        if bid_quote is not None and buy_size > 0:
            result[PRODUCT].append(Order(PRODUCT, int(bid_quote), int(buy_size)))

        if ask_quote is not None and sell_size > 0:
            result[PRODUCT].append(Order(PRODUCT, int(ask_quote), -int(sell_size)))

        payload = load_payload(read_trader_data(state))
        payload["version"] = 1
        payload[PRODUCT] = {
            "status": "ok",
            "position": position,
            "best_bid": best_bid_price,
            "best_ask": best_ask_price,
            "live_spread_ticks": live_spread,
            "spread_regime": spread_regime,
            "selected_order_size": selected_order_size,
            "base_bid": base_bid,
            "base_ask": base_ask,
            "pre_guardrail_bid": pre_guardrail_bid,
            "pre_guardrail_ask": pre_guardrail_ask,
            "bid_quote_mode": bid_quote_mode,
            "ask_quote_mode": ask_quote_mode,
            "chosen_bid": bid_quote,
            "chosen_ask": ask_quote,
            "buy_size": buy_size if bid_quote is not None else 0,
            "sell_size": sell_size if ask_quote is not None else 0,
            "config": simple_osmium_config_as_dict(self.config),
        }
        trader_data = dump_payload(payload)
        return result, 0, trader_data
