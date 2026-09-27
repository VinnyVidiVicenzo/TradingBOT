"""Price and quantity rounding, and filter enforcement."""

from __future__ import annotations

import unittest
from decimal import Decimal

from crypto_research.binanceus.filters import OrderType, RuleViolation
from crypto_research.decimals import ceil_to_step, dec, floor_to_step, step_to_places

from .helpers import default_rule_book, make_rule_entry, make_rules


class StepArithmeticTests(unittest.TestCase):
    def test_floor_to_step(self) -> None:
        self.assertEqual(floor_to_step(dec("0.123456789"), dec("0.00001")), dec("0.12345"))
        self.assertEqual(floor_to_step(dec("10.7"), dec("1")), dec("10"))
        self.assertEqual(floor_to_step(dec("0.000009"), dec("0.00001")), dec("0"))

    def test_zero_step_means_unconstrained(self) -> None:
        # Binance.US MARKET_LOT_SIZE.stepSize is often "0.00000000".
        value = dec("0.123456789")
        self.assertEqual(floor_to_step(value, dec("0")), value)
        self.assertEqual(ceil_to_step(value, dec("0")), value)

    def test_ceil_to_step(self) -> None:
        self.assertEqual(ceil_to_step(dec("0.12341"), dec("0.001")), dec("0.124"))
        self.assertEqual(ceil_to_step(dec("0.124"), dec("0.001")), dec("0.124"))

    def test_step_to_places(self) -> None:
        self.assertEqual(step_to_places(dec("0.01")), 2)
        self.assertEqual(step_to_places(dec("0.00001")), 5)
        self.assertEqual(step_to_places(dec("1")), 0)

    def test_no_float_contamination(self) -> None:
        # 0.1 + 0.2 must be exactly 0.3 here, which it is not in binary floats.
        self.assertEqual(dec("0.1") + dec("0.2"), dec("0.3"))


class SymbolRoundingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = default_rule_book().get("BTCUSD")

    def test_price_rounds_down_to_tick(self) -> None:
        self.assertEqual(self.rules.round_price(dec("42123.456789")), dec("42123.45"))

    def test_quantity_rounds_down_to_step(self) -> None:
        self.assertEqual(self.rules.round_quantity(dec("0.123456789"), OrderType.MARKET),
                         dec("0.12345"))

    def test_market_zero_step_does_not_zero_the_quantity(self) -> None:
        # Regression guard: dividing by a zero MARKET_LOT_SIZE step, or
        # flooring to it, would silently destroy every market order.
        quantity = self.rules.round_quantity(dec("0.00234"), OrderType.MARKET)
        self.assertEqual(quantity, dec("0.00234"))

    def test_market_step_coarser_than_lot_step_is_also_applied(self) -> None:
        book = make_rules([
            make_rule_entry("BTCUSD", base="BTC", quote="USD",
                            step="0.00001", market_step="0.001")
        ])
        rules = book.get("BTCUSD")
        self.assertEqual(rules.round_quantity(dec("0.12345"), OrderType.LIMIT), dec("0.12345"))
        self.assertEqual(rules.round_quantity(dec("0.12345"), OrderType.MARKET), dec("0.123"))

    def test_max_affordable_quantity(self) -> None:
        quantity = self.rules.max_affordable_quantity(
            dec("50"), dec("40000"), OrderType.MARKET
        )
        self.assertEqual(quantity, dec("0.00125"))
        self.assertLessEqual(quantity * dec("40000"), dec("50"))

    def test_derived_precision_is_read_from_the_snapshot(self) -> None:
        self.assertEqual(self.rules.price_places, 2)
        self.assertEqual(self.rules.quantity_places, 5)


class FilterCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.book = default_rule_book()
        self.rules = self.book.get("BTCUSD")

    def test_valid_market_order_passes(self) -> None:
        result = self.rules.check(
            price=dec("40000"), quantity=dec("0.00125"), order_type=OrderType.MARKET
        )
        self.assertTrue(result.ok, result.violations)
        self.assertEqual(result.notional, dec("50.00000"))

    def test_notional_below_minimum_is_rejected(self) -> None:
        result = self.rules.check(
            price=dec("40000"), quantity=dec("0.00002"), order_type=OrderType.MARKET
        )
        self.assertFalse(result.ok)
        self.assertIn(RuleViolation.NOTIONAL_BELOW_MIN, result.violations)

    def test_quantity_below_minimum_is_rejected(self) -> None:
        result = self.rules.check(
            price=dec("40000"), quantity=dec("0.000001"), order_type=OrderType.MARKET
        )
        self.assertFalse(result.ok)
        self.assertIn(RuleViolation.QUANTITY_BELOW_MIN, result.violations)

    def test_quantity_off_step_is_rejected(self) -> None:
        result = self.rules.check(
            price=dec("40000"), quantity=dec("0.000015"), order_type=OrderType.MARKET
        )
        self.assertFalse(result.ok)
        self.assertIn(RuleViolation.QUANTITY_NOT_ON_STEP, result.violations)

    def test_limit_price_off_tick_is_rejected(self) -> None:
        result = self.rules.check(
            price=dec("40000.001"), quantity=dec("0.001"), order_type=OrderType.LIMIT
        )
        self.assertFalse(result.ok)
        self.assertIn(RuleViolation.PRICE_NOT_ON_TICK, result.violations)

    def test_market_orders_are_not_subject_to_price_filter(self) -> None:
        # PRICE_FILTER constrains an order's price field; a MARKET order has
        # none, so an off-tick fill price must not be treated as a violation.
        result = self.rules.check(
            price=dec("40000.001"), quantity=dec("0.001"), order_type=OrderType.MARKET
        )
        self.assertTrue(result.ok, result.violations)

    def test_halted_symbol_is_rejected(self) -> None:
        book = make_rules([
            make_rule_entry("BTCUSD", base="BTC", quote="USD", status="BREAK")
        ])
        result = book.get("BTCUSD").check(
            price=dec("40000"), quantity=dec("0.001"), order_type=OrderType.MARKET
        )
        self.assertFalse(result.ok)
        self.assertIn(RuleViolation.SYMBOL_NOT_TRADING, result.violations)

    def test_non_spot_symbol_is_rejected(self) -> None:
        book = make_rules([
            make_rule_entry("BTCUSD", base="BTC", quote="USD", spot=False)
        ])
        result = book.get("BTCUSD").check(
            price=dec("40000"), quantity=dec("0.001"), order_type=OrderType.MARKET
        )
        self.assertFalse(result.ok)
        self.assertIn(RuleViolation.SPOT_NOT_ALLOWED, result.violations)

    def test_max_notional_is_enforced_when_supplied(self) -> None:
        book = make_rules([
            make_rule_entry("BTCUSD", base="BTC", quote="USD",
                            min_notional="1", max_notional="100",
                            use_modern_notional=True)
        ])
        result = book.get("BTCUSD").check(
            price=dec("40000"), quantity=dec("0.1"), order_type=OrderType.MARKET
        )
        self.assertFalse(result.ok)
        self.assertIn(RuleViolation.NOTIONAL_ABOVE_MAX, result.violations)

    def test_unknown_symbol_raises_rather_than_defaulting(self) -> None:
        # Guessing a tick size would make a backtest silently wrong.
        from crypto_research.errors import FilterParseError

        with self.assertRaises(FilterParseError):
            self.book.get("DOGEUSD")
        self.assertIsNone(self.book.maybe("DOGEUSD"))

    def test_tradable_listing(self) -> None:
        self.assertEqual(self.book.tradable(), ("BTCUSD", "ETHUSD", "USDTUSD"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
