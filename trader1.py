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
def get_fair_value(order_depth: Mapping[str, List[Order]]) -> Dict[str, float]:
    fair_values = {}
    for product, orders in order_depth.items():
        if not orders:
            continue
        buy_prices = [order.price for order in orders if order.quantity > 0]
        sell_prices = [order.price for order in orders if order.quantity < 0]
        if buy_prices and sell_prices:
            fair_value = (max(buy_prices) + min(sell_prices)) / 2
            fair_values[product] = fair_value
    return fair_values

class Trader(ABC):
    @abstractmethod
    def run(self, state: TradingState) -> Dict[str, List[Order]]:
        for product in state.order_depths:
            if product=="TOMATOES":
                bids=state.order_depths[product].buy_orders
                asks=state.order_depths[product].sell_orders
                fair_value=10000
                for key in bids:
                    if key>fair_value:
                        order=Order(product, key, bids[key])
                        return {product:[order]}
                for key in asks:
                    if key<fair_value:
                        order=Order(product, key, asks[key])
                        return {product:[order]}
