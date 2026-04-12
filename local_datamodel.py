from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Listing:
    symbol: str
    product: str
    denomination: str


@dataclass(frozen=True)
class Order:
    symbol: str
    price: int
    quantity: int


@dataclass
class OrderDepth:
    buy_orders: Dict[int, int] = field(default_factory=dict)
    sell_orders: Dict[int, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Trade:
    symbol: str
    price: int
    quantity: int
    buyer: Optional[str] = None
    seller: Optional[str] = None
    timestamp: int = 0


@dataclass
class TradingState:
    timestamp: int = 0
    listings: Dict[str, Listing] = field(default_factory=dict)
    order_depths: Dict[str, OrderDepth] = field(default_factory=dict)
    own_trades: Dict[str, List[Trade]] = field(default_factory=dict)
    market_trades: Dict[str, List[Trade]] = field(default_factory=dict)
    position: Dict[str, int] = field(default_factory=dict)
    observations: Any = None
    traderData: str = ""
