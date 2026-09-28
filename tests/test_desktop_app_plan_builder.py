"""desktop_app.plan_builder -- pure config-dict construction, no tkinter."""

from __future__ import annotations

import unittest
from decimal import Decimal

from crypto_research.config import parse_config
from crypto_research.timeutil import DAY_MS, add_months, parse_time

from desktop_app import plan_builder
from desktop_app.plan_builder import PlanRequest, Schedule

START = "2025-03-01T00:00:00Z"
END = "2026-03-01T00:00:00Z"


def _request(schedule: Schedule, amount: str = "200") -> PlanRequest:
    return PlanRequest(
        amount=Decimal(amount),
        symbols=("BTCUSDT", "ETHUSDT"),
        schedule=schedule,
        start=START,
        end=END,
    )


class BuildConfigPayloadTests(unittest.TestCase):
    def test_all_at_once_is_a_single_tranche_paid_in_usdt(self) -> None:
        config = parse_config(plan_builder.build_config_payload(_request(Schedule.ALL_AT_ONCE)))
        self.assertEqual(config.strategy_name, "benchmark1_staged_deployment")
        self.assertEqual(config.strategy_params["tranche_count"], 1)
        self.assertEqual(config.strategy_params["quote_asset"], "USDT")
        self.assertEqual(config.backtest.valuation_asset, "USDT")
        self.assertEqual(config.backtest.initial_cash, {"USDT": Decimal("200")})

    def test_spread_over_weeks_is_twelve_weekly_tranches(self) -> None:
        config = parse_config(
            plan_builder.build_config_payload(_request(Schedule.SPREAD_OVER_WEEKS))
        )
        self.assertEqual(config.strategy_params["tranche_count"], 12)
        self.assertEqual(config.strategy_params["cadence_days"], 7)

    def test_add_gradually_uses_contributions_not_initial_cash(self) -> None:
        config = parse_config(plan_builder.build_config_payload(_request(Schedule.ADD_GRADUALLY)))
        self.assertEqual(config.strategy_name, "benchmark2_contribution_dca")
        self.assertEqual(config.backtest.initial_cash, {})
        self.assertEqual(len(config.backtest.external_contributions), 12)

    def test_the_monthly_deposits_add_up_to_exactly_what_was_typed(self) -> None:
        for amount in ("1", "12", "20", "33.33", "200", "1234.56", "99999.99"):
            with self.subTest(amount=amount):
                config = parse_config(
                    plan_builder.build_config_payload(_request(Schedule.ADD_GRADUALLY, amount))
                )
                deposits = [c.amount for c in config.backtest.external_contributions]
                self.assertEqual(sum(deposits, Decimal(0)), Decimal(amount))
                self.assertTrue(all(d > 0 for d in deposits))

    def test_deposits_land_on_consecutive_calendar_months_a_day_after_each_month_starts(self) -> None:
        config = parse_config(plan_builder.build_config_payload(_request(Schedule.ADD_GRADUALLY)))
        times = [c.availability_time for c in config.backtest.external_contributions]
        start = parse_time(START)
        self.assertEqual(times, [add_months(start, i) + DAY_MS for i in range(12)])

    def test_no_deposit_is_credited_before_the_first_price_mark_of_the_run(self) -> None:
        # The first bar of a run closes one day (less a millisecond) after the
        # start; that close is the first price mark. The engine now counts a
        # deposit at or before it correctly, so no deposit has to fall after
        # it any more. The plan builder still keeps every deposit after it,
        # and this pins that.
        config = parse_config(plan_builder.build_config_payload(_request(Schedule.ADD_GRADUALLY)))
        first_mark = parse_time(START) + DAY_MS - 1
        self.assertTrue(
            all(c.availability_time > first_mark for c in config.backtest.external_contributions)
        )


class ParseAmountTests(unittest.TestCase):
    def test_accepts_plain_and_dollar_formatted_numbers(self) -> None:
        self.assertEqual(plan_builder.parse_amount("200"), Decimal("200"))
        self.assertEqual(plan_builder.parse_amount("$1,200.50"), Decimal("1200.50"))
        self.assertEqual(plan_builder.parse_amount("  75 "), Decimal("75"))

    def test_rejects_non_numeric_input(self) -> None:
        for text in ("abc", "", "12abc", "NaN", "Infinity"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                plan_builder.parse_amount(text)

    def test_rejects_zero_negative_and_below_the_minimum(self) -> None:
        for text in ("0", "-5", "0.99"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                plan_builder.parse_amount(text)

    def test_the_minimum_itself_is_accepted(self) -> None:
        self.assertEqual(plan_builder.parse_amount("1"), Decimal("1"))


if __name__ == "__main__":
    unittest.main()
