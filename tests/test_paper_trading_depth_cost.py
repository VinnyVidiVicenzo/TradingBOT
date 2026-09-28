"""Reading the live order book, honestly: a real cost, or an honest "cannot tell"."""

from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from paper_trading.depth_cost import DepthCostMeasurement, append, measure_buy_cost, read_all


class FakeBook:
    def __init__(self, asks=(), bids=()) -> None:
        self.asks = list(asks)
        self.bids = list(bids)

    def depth(self, symbol: str, limit: int = 100):
        return {"asks": self.asks, "bids": self.bids}


THIN_BOOK = FakeBook(
    asks=[["100.00", "1"], ["100.50", "1"], ["101.00", "10"]],
    bids=[["99.50", "1"], ["99.00", "5"]],
)


class MeasurementTests(unittest.TestCase):
    def test_the_mid_price_is_the_midpoint_of_the_best_bid_and_ask(self) -> None:
        m = measure_buy_cost(THIN_BOOK, "BTCUSD", Decimal("10"), at=1)
        self.assertEqual(m.mid_price, Decimal("99.75"))

    def test_a_small_order_fills_entirely_at_the_best_ask(self) -> None:
        m = measure_buy_cost(THIN_BOOK, "BTCUSD", Decimal("10"), at=1)
        self.assertEqual(m.average_price, Decimal("100.00"))
        self.assertTrue(m.cost_fraction > 0)

    def test_an_order_that_walks_two_levels_averages_them_by_volume(self) -> None:
        m = measure_buy_cost(THIN_BOOK, "BTCUSD", Decimal("150"), at=1)
        # 1 unit at 100.00 (=100) + the rest at 100.50 until 150 is spent.
        expected_qty = Decimal(1) + (Decimal(50) / Decimal("100.50"))
        expected_avg = Decimal(150) / expected_qty
        self.assertAlmostEqual(float(m.average_price), float(expected_avg), places=8)

    def test_a_book_too_thin_to_fill_the_order_says_so_rather_than_extrapolating(self) -> None:
        m = measure_buy_cost(THIN_BOOK, "BTCUSD", Decimal("1_000_000"), at=1)
        self.assertTrue(m.depth_available)
        self.assertIsNone(m.average_price)
        self.assertIsNone(m.cost_fraction)

    def test_an_empty_book_is_reported_as_unavailable_not_as_free(self) -> None:
        m = measure_buy_cost(FakeBook(), "BTCUSD", Decimal("10"), at=1)
        self.assertFalse(m.depth_available)
        self.assertIsNone(m.cost_fraction)

    def test_a_one_sided_book_is_also_unavailable(self) -> None:
        m = measure_buy_cost(FakeBook(asks=[["100", "1"]]), "BTCUSD", Decimal("10"), at=1)
        self.assertFalse(m.depth_available)

    def test_notional_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            measure_buy_cost(THIN_BOOK, "BTCUSD", Decimal("0"), at=1)
        with self.assertRaises(ValueError):
            measure_buy_cost(THIN_BOOK, "BTCUSD", Decimal("-5"), at=1)



class RoundTripTests(unittest.TestCase):
    def test_to_dict_and_from_dict_round_trip(self) -> None:
        m = measure_buy_cost(THIN_BOOK, "BTCUSD", Decimal("150"), at=42)
        again = DepthCostMeasurement.from_dict(m.to_dict())
        self.assertEqual(m, again)

    def test_to_dict_is_json_safe_including_when_unavailable(self) -> None:
        m = measure_buy_cost(FakeBook(), "BTCUSD", Decimal("10"), at=1)
        json.dumps(m.to_dict())


class LogFileTests(unittest.TestCase):
    def test_measurements_append_and_read_back_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cost.jsonl"
            first = measure_buy_cost(THIN_BOOK, "BTCUSD", Decimal("10"), at=1)
            second = measure_buy_cost(THIN_BOOK, "ETHUSD", Decimal("20"), at=2)
            append(path, first)
            append(path, second)
            entries = read_all(path)
            self.assertEqual([e.symbol for e in entries], ["BTCUSD", "ETHUSD"])
            self.assertEqual(entries[0].at, 1)

    def test_reading_a_file_that_does_not_exist_yet_is_an_empty_list(self) -> None:
        self.assertEqual(read_all("/no/such/file.jsonl"), [])


if __name__ == "__main__":
    unittest.main()
