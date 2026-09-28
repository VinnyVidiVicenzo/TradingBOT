"""desktop_app.sweep -- the plan tried from every possible starting month.

The synthetic tests build a small throwaway store on disk and check each rule
in isolation. The last class replays the earlier research against this repo's
real local data and skips itself when that data isn't present.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from typing import Callable

from crypto_research.binanceus.parsing import parse_symbol_rules
from crypto_research.errors import ConfigError
from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository, RuleSnapshotRepository
from crypto_research.timeutil import DAY_MS, parse_time, to_datetime, to_iso

from desktop_app import coin_catalog, sweep
from desktop_app.plan_builder import Schedule
from desktop_app.sweep import SweepRequest

from .helpers import make_bar, make_rule_entry

D = Decimal
START = parse_time("2024-01-01T00:00:00Z")  # 2024 is a leap year: Jan 1 .. May 29 is 150 days
DAYS = 150
LAST_OPEN = START + (DAYS - 1) * DAY_MS
REPO_ROOT = Path(__file__).parent.parent


def rising(i: int) -> str:
    return str(100 + i)


def flat(_i: int) -> str:
    return "100"


def build_store(
    path: Path,
    *,
    days: int = DAYS,
    price: Callable[[int], str] = rising,
    rules_for: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
    eth_missing: range = range(0),
) -> None:
    db = open_database(str(path))
    try:
        RuleSnapshotRepository(db).upsert_many(
            parse_symbol_rules(
                make_rule_entry(symbol, base=symbol[:-4], quote="USDT"),
                received_at=0,
                valid_from=0,
            )
            for symbol in rules_for
        )
        bars = BarRepository(db)
        for symbol in ("BTCUSDT", "ETHUSDT"):
            bars.upsert_many(
                make_bar(symbol, START + i * DAY_MS, price(i))
                for i in range(days)
                if not (symbol == "ETHUSDT" and i in eth_missing)
            )
    finally:
        db.close()


def request(path: Path, **overrides) -> SweepRequest:
    values = dict(
        amount=D("100"),
        symbols=("BTCUSDT", "ETHUSDT"),
        schedule=Schedule.ALL_AT_ONCE,
        hold_months=1,
        first_open_ms=START,
        last_open_ms=LAST_OPEN,
        db_path=str(path),
    )
    values.update(overrides)
    return SweepRequest(**values)


class MonthStartsTests(unittest.TestCase):
    def test_the_real_research_window_gives_sixty_one_year_starts(self) -> None:
        starts = sweep.month_starts(
            parse_time("2020-10-01T00:00:00Z"), parse_time("2026-09-26T00:00:00Z"), 12
        )
        self.assertEqual(len(starts), 60)
        self.assertEqual(starts[0], parse_time("2020-10-01T00:00:00Z"))
        self.assertEqual(starts[-1], parse_time("2025-09-01T00:00:00Z"))

    def test_a_mid_month_first_bar_rounds_up_to_the_next_month(self) -> None:
        starts = sweep.month_starts(parse_time("2024-01-15T00:00:00Z"), LAST_OPEN, 1)
        self.assertEqual(starts[0], parse_time("2024-02-01T00:00:00Z"))

    def test_every_start_is_the_first_of_a_month(self) -> None:
        for start in sweep.month_starts(START, LAST_OPEN, 1):
            self.assertEqual(to_datetime(start).day, 1)
            self.assertEqual(to_datetime(start).hour, 0)

    def test_a_hold_longer_than_the_history_yields_nothing(self) -> None:
        self.assertEqual(sweep.month_starts(START, LAST_OPEN, 12), [])

    def test_the_last_window_may_end_one_day_past_the_final_bar(self) -> None:
        # The bar opening on the last day of a hold is the last one needed, so a
        # hold ending at midnight the day after the final bar is still complete.
        last_open = parse_time("2024-01-31T00:00:00Z")
        starts = sweep.month_starts(START, last_open, 1)
        self.assertEqual(starts, [START])
        self.assertEqual(sweep.month_starts(START, last_open - DAY_MS, 1), [])


class RunSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "market.sqlite"

    def test_a_rising_market_ends_above_what_was_put_in_every_month(self) -> None:
        build_store(self.path)
        result = sweep.run_sweep(request(self.path))
        self.assertEqual(len(result.outcomes), 4)  # Jan, Feb, Mar, Apr starts
        self.assertEqual(result.skipped, 0)
        for outcome in result.outcomes:
            self.assertEqual(outcome.capital_in, D("100"))
            self.assertGreater(outcome.ending_value, D("100"))
            self.assertEqual(outcome.rejected_orders, 0)

    def test_a_flat_market_loses_only_the_cost_of_buying(self) -> None:
        build_store(self.path, price=flat)
        result = sweep.run_sweep(request(self.path))
        for outcome in result.outcomes:
            self.assertLess(outcome.ending_value, D("100"))
            self.assertGreater(outcome.ending_value, D("98"))

    def test_outcomes_are_in_start_order(self) -> None:
        build_store(self.path)
        starts = [o.start_ms for o in sweep.run_sweep(request(self.path)).outcomes]
        self.assertEqual(starts, sorted(starts))

    def test_the_lump_sum_schedules_put_in_exactly_the_amount(self) -> None:
        build_store(self.path)
        for schedule in (Schedule.ALL_AT_ONCE, Schedule.SPREAD_OVER_WEEKS):
            with self.subTest(schedule=schedule):
                result = sweep.run_sweep(request(self.path, schedule=schedule))
                self.assertTrue(result.outcomes)
                for outcome in result.outcomes:
                    self.assertEqual(outcome.capital_in, D("100"))

    def test_the_monthly_schedule_puts_in_the_whole_amount_over_a_year(self) -> None:
        # Twelve deposits, one a month, so only a hold of a year or more can
        # take all of them: 450 days of prices give three 12-month windows.
        build_store(self.path, days=450)
        result = sweep.run_sweep(
            request(
                self.path,
                schedule=Schedule.ADD_GRADUALLY,
                hold_months=12,
                last_open_ms=START + 449 * DAY_MS,
            )
        )
        self.assertEqual(len(result.outcomes), 3)
        for outcome in result.outcomes:
            self.assertEqual(outcome.capital_in, D("100"))
            self.assertGreater(outcome.ending_value, D("100"))

    def test_the_result_records_what_was_asked(self) -> None:
        build_store(self.path)
        result = sweep.run_sweep(request(self.path, schedule=Schedule.SPREAD_OVER_WEEKS))
        self.assertEqual(result.amount, D("100"))
        self.assertEqual(result.symbols, ("BTCUSDT", "ETHUSDT"))
        self.assertIs(result.schedule, Schedule.SPREAD_OVER_WEEKS)
        self.assertEqual(result.hold_months, 1)

    def test_a_hole_in_one_coins_prices_skips_only_the_months_it_touches(self) -> None:
        # ETH has no prices on days 40-69 (Feb 10 - Mar 10): the Feb and Mar
        # holds run through the hole and are skipped; Jan and Apr are intact.
        build_store(self.path, eth_missing=range(40, 70))
        result = sweep.run_sweep(request(self.path))
        self.assertEqual(len(result.outcomes), 2)
        self.assertEqual(result.skipped, 2)

    def test_a_coin_whose_prices_start_late_skips_the_months_before_it(self) -> None:
        # ETH's first price is day 20 (Jan 21). load_dataset alone would accept
        # a January hold -- there is no hole -- so this needs the edge check.
        build_store(self.path, eth_missing=range(0, 20))
        result = sweep.run_sweep(request(self.path))
        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(result.outcomes), 3)
        self.assertNotIn(START, [o.start_ms for o in result.outcomes])

    def test_a_small_amount_reports_buys_the_exchange_would_refuse(self) -> None:
        build_store(self.path)
        # $1 spread over 12 weeks and two coins is about 4 cents a buy -- far
        # below the $1 minimum order, so every buy is refused.
        result = sweep.run_sweep(
            request(self.path, amount=D("1"), schedule=Schedule.SPREAD_OVER_WEEKS)
        )
        self.assertTrue(result.outcomes)
        self.assertTrue(all(o.rejected_orders > 0 for o in result.outcomes))
        # ...and none of the money ever left cash.
        self.assertTrue(all(o.ending_value == D("1") for o in result.outcomes))

    def test_missing_rules_for_every_coin_is_an_error_not_an_empty_result(self) -> None:
        build_store(self.path, rules_for=())
        with self.assertRaises(ConfigError) as caught:
            sweep.run_sweep(request(self.path))
        self.assertIn("no exchangeInfo snapshots", str(caught.exception))

    def test_missing_rules_for_one_coin_names_it(self) -> None:
        build_store(self.path, rules_for=("BTCUSDT",))
        with self.assertRaises(ConfigError) as caught:
            sweep.run_sweep(request(self.path))
        self.assertIn("ETHUSDT", str(caught.exception))
        self.assertIn("no Binance.US trading rules stored for", str(caught.exception))

    def test_no_possible_window_is_an_empty_result_not_an_error(self) -> None:
        build_store(self.path)
        result = sweep.run_sweep(request(self.path, hold_months=12))
        self.assertEqual(result.outcomes, ())
        self.assertEqual(result.skipped, 0)


class ResearchRegressionTests(unittest.TestCase):
    """The same plan, coins and windows as the earlier research must give the same money.

    results/start_date_sweep_12m.csv is committed. It holds, for each of 60
    starting months, what $200 in BTC/ETH/SOL ended at when put in all at once,
    and when spread over 12 weekly buys. Skipped when the local price data
    isn't present (it is not part of the repository).
    """

    DB = REPO_ROOT / "data" / "market.sqlite"
    CSV = REPO_ROOT / "results" / "start_date_sweep_12m.csv"

    @unittest.skipUnless(DB.exists() and CSV.exists(), "local price data / research CSV not present")
    def test_the_sweep_reproduces_the_earlier_research_to_the_cent(self) -> None:
        with self.CSV.open(encoding="utf-8") as handle:
            expected = {row["start"]: row for row in csv.DictReader(handle)}
        options = [
            option
            for option in coin_catalog.discover_available_coins(str(self.DB))
            if option.base_asset in ("BTC", "ETH", "SOL")
        ]
        self.assertEqual(len(options), 3)
        first, last = coin_catalog.overlapping_range(options)
        for schedule, column in (
            (Schedule.ALL_AT_ONCE, "all_at_once"),
            (Schedule.SPREAD_OVER_WEEKS, "spread_12_weekly"),
        ):
            with self.subTest(schedule=schedule):
                result = sweep.run_sweep(
                    SweepRequest(
                        amount=D("200"),
                        symbols=tuple(option.symbol for option in options),
                        schedule=schedule,
                        hold_months=12,
                        first_open_ms=first,
                        last_open_ms=last,
                        db_path=str(self.DB),
                    )
                )
                self.assertEqual(result.skipped, 0)
                # Months added to the local data since the CSV was written are
                # extra windows; the CSV's own 60 must all be present and exact.
                by_month = {to_iso(o.start_ms)[:7]: o for o in result.outcomes}
                self.assertGreaterEqual(len(by_month), len(expected))
                for month, row in expected.items():
                    self.assertIn(month, by_month)
                    self.assertLess(
                        abs(by_month[month].ending_value - D(row[column])),
                        D("0.01"),
                        f"{schedule.value} starting {month}",
                    )


if __name__ == "__main__":
    unittest.main()
