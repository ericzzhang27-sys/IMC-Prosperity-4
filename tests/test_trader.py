from __future__ import annotations

import csv
import unittest
from pathlib import Path

from core.persistence import dump_trader_data, load_trader_data
from core.risk import clip_orders_to_position_limit, violates_position_limit
from core.strategy import estimate_fair_value
from local_datamodel import Order, OrderDepth, TradingState
from trader import Trader


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def make_state(
    order_depths: dict[str, OrderDepth] | None = None,
    position: dict[str, int] | None = None,
    trader_data: str = "",
) -> TradingState:
    return TradingState(
        timestamp=0,
        listings={},
        order_depths=order_depths or {},
        own_trades={},
        market_trades={},
        position=position or {},
        observations=None,
        traderData=trader_data,
    )


def order_depth_from_row(row: dict[str, str]) -> OrderDepth:
    buy_orders: dict[int, int] = {}
    sell_orders: dict[int, int] = {}

    for level in (1, 2, 3):
        bid_price = row.get(f"bid_price_{level}", "")
        bid_volume = row.get(f"bid_volume_{level}", "")
        ask_price = row.get(f"ask_price_{level}", "")
        ask_volume = row.get(f"ask_volume_{level}", "")

        if bid_price and bid_volume:
            buy_orders[int(bid_price)] = int(bid_volume)
        if ask_price and ask_volume:
            sell_orders[int(ask_price)] = int(ask_volume)

    return OrderDepth(buy_orders=buy_orders, sell_orders=sell_orders)


class TraderTests(unittest.TestCase):
    def test_handles_missing_state_fields_gracefully(self) -> None:
        trader = Trader()
        state = {"order_depths": {"EMERALDS": {"buy_orders": {}, "sell_orders": {}}}}

        result, conversions, trader_data = trader.run(state)

        self.assertEqual(result["EMERALDS"], [])
        self.assertEqual(conversions, 0)
        self.assertIsInstance(trader_data, str)

    def test_empty_order_book_handling(self) -> None:
        trader = Trader()
        state = make_state(order_depths={"EMERALDS": OrderDepth()})

        result, conversions, trader_data = trader.run(state)

        self.assertEqual(result["EMERALDS"], [])
        self.assertEqual(conversions, 0)
        self.assertIsInstance(trader_data, str)

    def test_fair_value_computation(self) -> None:
        depth = OrderDepth(buy_orders={9998: 5}, sell_orders={10002: 4})
        self.assertEqual(estimate_fair_value(depth), 10000.0)

    def test_limit_enforcement(self) -> None:
        orders = [
            Order("EMERALDS", 9999, 5),
            Order("EMERALDS", 10001, -3),
        ]

        clipped = clip_orders_to_position_limit(orders, current_position=19, position_limit=20)

        self.assertTrue(violates_position_limit(19, orders, 20))
        self.assertEqual(clipped[0].quantity, 1)
        self.assertEqual(clipped[1].quantity, -3)
        self.assertFalse(violates_position_limit(19, clipped, 20))

    def test_run_return_shape(self) -> None:
        trader = Trader()
        state = make_state(
            order_depths={
                "EMERALDS": OrderDepth(
                    buy_orders={9992: 10},
                    sell_orders={10008: 10},
                )
            }
        )

        result, conversions, trader_data = trader.run(state)

        self.assertIsInstance(result, dict)
        self.assertIsInstance(result["EMERALDS"], list)
        self.assertIsInstance(conversions, int)
        self.assertIsInstance(trader_data, str)

    def test_trader_data_round_trip(self) -> None:
        payload = {"version": 1, "fair_values": {"EMERALDS": 10000.0}}
        encoded = dump_trader_data(payload)
        decoded = load_trader_data(encoded)
        self.assertEqual(decoded, payload)

    def test_smoke_runs_on_example_price_schema(self) -> None:
        fixture_path = FIXTURE_DIR / "prices_sample.csv"
        trader = Trader()
        trader_data = ""

        with fixture_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            grouped_books: dict[int, dict[str, OrderDepth]] = {}

            for row in reader:
                synthetic_timestamp = int(row["timestamp"]) + (int(row["day"]) * 100000)
                grouped_books.setdefault(synthetic_timestamp, {})[row["product"]] = order_depth_from_row(row)

        for timestamp in sorted(grouped_books):
            state = TradingState(
                timestamp=timestamp,
                listings={},
                order_depths=grouped_books[timestamp],
                own_trades={},
                market_trades={},
                position={},
                observations=None,
                traderData=trader_data,
            )

            result, conversions, trader_data = trader.run(state)

            self.assertIsInstance(result, dict)
            self.assertEqual(conversions, 0)
            self.assertIsInstance(trader_data, str)
            self.assertIn("fair_values", load_trader_data(trader_data))


if __name__ == "__main__":
    unittest.main()
