"""desktop_app.alternatives -- the stock fund and the savings account as yardsticks.

Expected values are worked out here by hand from simple price paths, not by
calling the code under test, so a wrong convention cannot agree with itself.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from crypto_research.timeutil import DAY_MS, add_months, parse_time

from desktop_app import alternatives, sweep
from desktop_app.plan_builder import Schedule
from desktop_app.sweep import SweepResult, WindowOutcome
from desktop_app.sweep_summary import summarize_outcomes

D = Decimal
BASE = parse_time("2023-01-01T00:00:00Z")
START = parse_time("2023-03-01T00:00:00Z")
REPO_ROOT = Path(__file__).parent.parent


def reference(*, days: int = 1500, fund=lambda i: 100.0, yield_pct=lambda i: 0.0):
    prices = {BASE + i * DAY_MS: fund(i) for i in range(days)}
    yields = {BASE + i * DAY_MS: yield_pct(i) for i in range(days)}
    ref = alternatives.build_reference(prices, yields)
    assert ref is not None
    return ref


def result(schedule=Schedule.ALL_AT_ONCE, *, hold=12, starts=(START,), amount="200") -> SweepResult:
    outcomes = tuple(WindowOutcome(s, D(amount), D(amount), D(0), 0) for s in starts)
    return SweepResult(D(amount), ("BTCUSDT",), schedule, hold, outcomes, 0)


def first_last(ref, start=START, hold=12) -> tuple[int, int]:
    return ref.index(start), ref.index(add_months(start, hold) - DAY_MS)


def run(ref, schedule, *, hold=12, start=START):
    found = alternatives.compare(result(schedule, hold=hold, starts=(start,)), ref)
    assert found is not None
    return found.fund[0], found.savings[0]


class BuildReferenceTests(unittest.TestCase):
    def test_too_few_readings_is_not_usable(self) -> None:
        short = {BASE + i * DAY_MS: 100.0 for i in range(alternatives.MIN_ROWS - 1)}
        full = {BASE + i * DAY_MS: 1.0 for i in range(500)}
        self.assertIsNone(alternatives.build_reference(short, full))
        self.assertIsNone(alternatives.build_reference(full, short))

    def test_weekends_and_holidays_carry_the_last_close_forward(self) -> None:
        # Readings only on days 0-4 of every 7 (Mon-Fri), rising each day.
        prices = {BASE + i * DAY_MS: 100.0 + i for i in range(700) if i % 7 < 5}
        yields = {BASE + i * DAY_MS: 1.0 for i in range(700)}
        ref = alternatives.build_reference(prices, yields)
        friday, saturday, sunday, monday = 4, 5, 6, 7
        self.assertEqual(ref.fund[saturday], ref.fund[friday])
        self.assertEqual(ref.fund[sunday], ref.fund[friday])
        self.assertGreater(ref.fund[monday], ref.fund[friday])

    def test_the_savings_rate_is_the_annual_percent_spread_over_365_days(self) -> None:
        ref = reference(yield_pct=lambda i: 3.65)
        self.assertAlmostEqual(ref.daily_rate[10], 0.0001, places=12)

    def test_coverage_starts_at_the_later_of_the_two_first_dates(self) -> None:
        prices = {BASE + i * DAY_MS: 100.0 for i in range(100, 400)}
        yields = {BASE + i * DAY_MS: 1.0 for i in range(0, 400)}
        ref = alternatives.build_reference(prices, yields)
        self.assertEqual(ref.base_ms, BASE + 100 * DAY_MS)

    def test_a_few_days_past_the_last_reading_are_tolerated_but_not_more(self) -> None:
        ref = reference(days=800)
        last_real = BASE + 799 * DAY_MS
        allowed = alternatives.STALE_DAYS_ALLOWED
        self.assertTrue(ref.covers(BASE, last_real + allowed * DAY_MS))
        self.assertFalse(ref.covers(BASE, last_real + (allowed + 1) * DAY_MS))

    def test_a_day_before_the_data_begins_is_not_covered(self) -> None:
        ref = reference()
        self.assertFalse(ref.covers(BASE - DAY_MS, BASE + DAY_MS))


class LoadReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name: str, header: list[str], rows: list[list[object]]) -> None:
        with (self.dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerows(rows)

    def _good_files(self, *, extra_fund_rows=()) -> None:
        dates = [BASE + i * DAY_MS for i in range(300)]
        from crypto_research.timeutil import to_iso

        self._write(
            "spy_adjusted.csv", ["time", "date", "adjusted_close"],
            [[d, to_iso(d)[:10], 100.0 + i] for i, d in enumerate(dates)] + list(extra_fund_rows),
        )
        self._write(
            "tbill_3m.csv", ["date", "annual_yield_pct"],
            [[to_iso(d)[:10], 4.0] for d in dates],
        )

    def test_good_files_load(self) -> None:
        self._good_files()
        ref = alternatives.load_reference(self.dir)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.base_ms, BASE)

    def test_missing_files_mean_no_comparison_not_an_error(self) -> None:
        self.assertIsNone(alternatives.load_reference(self.dir))
        self.assertIsNone(alternatives.load_reference(self.dir / "nowhere"))

    def test_one_file_missing_is_also_no_comparison(self) -> None:
        self._good_files()
        (self.dir / "tbill_3m.csv").unlink()
        self.assertIsNone(alternatives.load_reference(self.dir))

    def test_unreadable_rows_are_skipped_and_reported_not_fatal(self) -> None:
        self._good_files(extra_fund_rows=[[0, "not-a-date", "oops"], [0, "2024-01-01", "n/a"]])
        with self.assertLogs("desktop_app.alternatives", level="WARNING") as logged:
            ref = alternatives.load_reference(self.dir)
        self.assertIsNotNone(ref)
        self.assertIn("skipped 2 unreadable row(s)", logged.output[0])

    def test_files_too_short_to_use_are_reported(self) -> None:
        self._write("spy_adjusted.csv", ["time", "date", "adjusted_close"], [[0, "2023-01-01", 100.0]])
        self._write("tbill_3m.csv", ["date", "annual_yield_pct"], [["2023-01-01", 4.0]])
        with self.assertLogs("desktop_app.alternatives", level="WARNING"):
            self.assertIsNone(alternatives.load_reference(self.dir))

    def test_a_file_with_the_wrong_columns_is_treated_as_unusable(self) -> None:
        self._write("spy_adjusted.csv", ["a", "b"], [[1, 2]] * 100)
        self._write("tbill_3m.csv", ["date", "annual_yield_pct"], [["2023-01-01", 4.0]] * 100)
        with self.assertLogs("desktop_app.alternatives", level="WARNING"):
            self.assertIsNone(alternatives.load_reference(self.dir))


class FundTests(unittest.TestCase):
    def test_a_flat_fund_returns_exactly_the_money_put_in(self) -> None:
        for schedule in Schedule:
            with self.subTest(schedule=schedule):
                fund, _ = run(reference(), schedule)
                self.assertAlmostEqual(float(fund.ending_value), 200.0, places=3)
                self.assertEqual(fund.max_drawdown, D("0"))

    def test_a_lump_sum_earns_the_fund_move_between_the_first_and_last_day(self) -> None:
        ref = reference(fund=lambda i: 100.0 + i)
        first, last = first_last(ref)
        fund, _ = run(ref, Schedule.ALL_AT_ONCE)
        expected = 200.0 * (100.0 + last) / (100.0 + first)
        self.assertAlmostEqual(float(fund.ending_value), expected, places=3)

    def test_the_worst_fall_from_a_high_is_found_even_after_a_full_recovery(self) -> None:
        first, _ = first_last(reference())
        ref = reference(fund=lambda i: 40.0 if first + 50 <= i < first + 100 else 100.0)
        fund, _ = run(ref, Schedule.ALL_AT_ONCE)
        self.assertAlmostEqual(float(fund.max_drawdown), 0.6, places=6)
        self.assertAlmostEqual(float(fund.ending_value), 200.0, places=3)

    def test_twelve_weekly_buys_are_made_on_the_plans_own_days(self) -> None:
        first, _ = first_last(reference())
        # Every buy (days 0, 7, ... 77) is made at 100, then the fund doubles.
        ref = reference(fund=lambda i: 100.0 if i < first + 100 else 200.0)
        fund, _ = run(ref, Schedule.SPREAD_OVER_WEEKS)
        self.assertAlmostEqual(float(fund.ending_value), 400.0, places=3)

    def test_buys_made_after_a_fall_get_the_lower_price(self) -> None:
        first, _ = first_last(reference())
        # Price halves on day 40: six weekly buys at 100 (days 0-35), six at 50 (days 42-77).
        # units = 6 * (200/12)/100 + 6 * (200/12)/50 = 1 + 2 = 3, worth 3 * 50 = 150.
        ref = reference(fund=lambda i: 100.0 if i < first + 40 else 50.0)
        fund, _ = run(ref, Schedule.SPREAD_OVER_WEEKS)
        self.assertAlmostEqual(float(fund.ending_value), 150.0, places=3)

    def test_the_monthly_schedule_buys_as_each_deposit_arrives(self) -> None:
        first, _ = first_last(reference())
        # All twelve deposits (the last around day 336) are in before the price doubles on day 350.
        ref = reference(fund=lambda i: 100.0 if i < first + 350 else 200.0)
        fund, _ = run(ref, Schedule.ADD_GRADUALLY)
        self.assertAlmostEqual(float(fund.ending_value), 400.0, places=3)

    def test_a_deposit_arriving_after_a_fall_does_not_hide_the_fall(self) -> None:
        first, _ = first_last(reference())
        # Halves on day 100 and stays there while deposits keep arriving. Measured per
        # fund-style unit the fall is a clean 50%, not diluted by the later deposits.
        ref = reference(fund=lambda i: 100.0 if i < first + 100 else 50.0)
        fund, _ = run(ref, Schedule.ADD_GRADUALLY)
        self.assertAlmostEqual(float(fund.max_drawdown), 0.5, places=6)

    def test_money_waiting_to_be_spent_earns_nothing_in_the_fund_plan(self) -> None:
        # A falling then recovering price, spread over 12 weeks: the total put in
        # never grows just by waiting, so a flat fund gives back exactly the stake.
        fund, _ = run(reference(), Schedule.SPREAD_OVER_WEEKS)
        self.assertAlmostEqual(float(fund.ending_value), 200.0, places=3)


class SavingsTests(unittest.TestCase):
    # 3.65% a year is exactly 0.01% a day.
    def test_a_lump_sum_earns_daily_interest_from_the_day_after_it_goes_in(self) -> None:
        ref = reference(yield_pct=lambda i: 3.65)
        first, last = first_last(ref)
        _, savings = run(ref, Schedule.ALL_AT_ONCE)
        self.assertAlmostEqual(float(savings.ending_value), 200.0 * 1.0001 ** (last - first), places=3)

    def test_spreading_the_buying_does_not_change_savings_the_whole_stake_sits_there(self) -> None:
        ref = reference(yield_pct=lambda i: 3.65)
        _, lump = run(ref, Schedule.ALL_AT_ONCE)
        _, spread = run(ref, Schedule.SPREAD_OVER_WEEKS)
        self.assertEqual(lump.ending_value, spread.ending_value)

    def test_each_monthly_deposit_earns_only_from_when_it_arrives(self) -> None:
        ref = reference(yield_pct=lambda i: 3.65)
        first, last = first_last(ref)
        per_month = 200 / 12
        # Deposits land a day after each month starts; the last one absorbs the rounding.
        deposits = [ref.index(add_months(START, m) + DAY_MS) for m in range(12)]
        expected = sum(per_month * 1.0001 ** (last - day) for day in deposits)
        _, savings = run(ref, Schedule.ADD_GRADUALLY)
        self.assertAlmostEqual(float(savings.ending_value), expected, delta=0.01)

    def test_a_change_in_the_rate_partway_through_is_followed(self) -> None:
        first, last = first_last(reference())
        switch = first + 100
        ref = reference(yield_pct=lambda i: 0.0 if i < switch else 3.65)
        _, savings = run(ref, Schedule.ALL_AT_ONCE)
        self.assertAlmostEqual(float(savings.ending_value), 200.0 * 1.0001 ** (last - switch + 1), places=3)

    def test_savings_never_falls(self) -> None:
        _, savings = run(reference(yield_pct=lambda i: 2.0), Schedule.ADD_GRADUALLY)
        self.assertEqual(savings.max_drawdown, D("0"))

    def test_a_zero_rate_gives_back_exactly_the_stake(self) -> None:
        for schedule in Schedule:
            with self.subTest(schedule=schedule):
                _, savings = run(reference(), schedule)
                self.assertAlmostEqual(float(savings.ending_value), 200.0, places=3)


class CoverageTests(unittest.TestCase):
    def test_a_month_before_the_data_begins_is_left_out(self) -> None:
        ref = reference()
        early = BASE - 40 * DAY_MS
        found = alternatives.compare(result(starts=(early, START)), ref)
        self.assertEqual([o.start_ms for o in found.fund], [START])

    def test_a_month_running_past_the_end_of_the_data_is_left_out(self) -> None:
        ref = reference(days=460)  # last reading around 2024-04-05; a hold from 2023-03-01 ends 2024-03-01
        late_start = START + 400 * DAY_MS
        found = alternatives.compare(result(starts=(START, late_start)), ref)
        self.assertEqual([o.start_ms for o in found.fund], [START])

    def test_a_weekend_after_the_last_reading_is_tolerated(self) -> None:
        first, last = first_last(reference())
        # Readings stop exactly 3 days before the plan's last day.
        ref = reference(days=last - 3 + 1)
        self.assertIsNotNone(alternatives.compare(result(), ref))

    def test_a_gap_longer_than_that_is_not(self) -> None:
        first, last = first_last(reference())
        ref = reference(days=last - alternatives.STALE_DAYS_ALLOWED - 1 + 1)
        self.assertIsNone(alternatives.compare(result(), ref))

    def test_nothing_covered_is_none_not_an_empty_comparison(self) -> None:
        ref = reference(days=200)
        self.assertIsNone(alternatives.compare(result(), ref))

    def test_both_yardsticks_use_the_same_months_and_the_plans_own_stake(self) -> None:
        starts = tuple(START + i * 31 * DAY_MS for i in range(4))
        found = alternatives.compare(result(starts=starts, amount="350"), reference())
        self.assertEqual([o.start_ms for o in found.fund], [o.start_ms for o in found.savings])
        for outcome in found.fund + found.savings:
            self.assertEqual(outcome.capital_in, D("350"))
            self.assertEqual(outcome.rejected_orders, 0)

    def test_the_summary_functions_accept_the_yardsticks_like_any_other_result(self) -> None:
        found = alternatives.compare(result(starts=(START, START + 40 * DAY_MS)), reference())
        summary = summarize_outcomes(found.fund, amount=D("200"))
        self.assertEqual(summary.tested, 2)
        self.assertEqual(summary.lost_money, 0)


class ResearchRegressionTests(unittest.TestCase):
    """The yardsticks must agree with what the earlier research published.

    results/mix_vs_savings_report.txt and CONTEXT_FOR_CLAUDE.md give, for $200 put in
    all at once in every starting month, a 1-year stock fund with a typical result of
    $235-236, money lost in 11-12 of 60 starting months and a worst fall of 24%, and
    savings at a typical $208 (and 2 years: fund lost money in 1 of 48). The reference
    files are committed, so this runs on any checkout.
    """

    DIRECTORY = REPO_ROOT / "data" / "reference"

    @unittest.skipUnless(
        (DIRECTORY / "spy_adjusted.csv").exists() and (DIRECTORY / "tbill_3m.csv").exists(),
        "reference data not present",
    )
    def test_matches_the_published_one_and_two_year_results(self) -> None:
        ref = alternatives.load_reference(self.DIRECTORY)
        self.assertIsNotNone(ref)
        for hold, expected_months, typical_range, lost_range in (
            (12, 60, (234, 237), (10, 12)),
            (24, 48, (274, 277), (1, 1)),
        ):
            starts = tuple(sweep.month_starts(
                parse_time("2020-10-01T00:00:00Z"), parse_time("2026-09-26T00:00:00Z"), hold
            ))
            with self.subTest(hold=hold):
                found = alternatives.compare(result(hold=hold, starts=starts), ref)
                fund = summarize_outcomes(found.fund, amount=D("200"))
                savings = summarize_outcomes(found.savings, amount=D("200"))
                self.assertEqual(fund.tested, expected_months)
                self.assertTrue(typical_range[0] <= fund.typical_ending <= typical_range[1], fund.typical_ending)
                self.assertTrue(lost_range[0] <= fund.lost_money <= lost_range[1], fund.lost_money)
                self.assertEqual(savings.lost_money, 0)
                if hold == 12:
                    self.assertTrue(D("207") <= savings.typical_ending <= D("209"), savings.typical_ending)
                    worst_fall = max(o.max_drawdown for o in found.fund)
                    self.assertTrue(D("0.23") <= worst_fall <= D("0.25"), worst_fall)


if __name__ == "__main__":
    unittest.main()
