"""The scorecard: mechanical checks only, never a performance grade."""

from __future__ import annotations

import unittest

from paper_trading.runner import STATUS_FEED_ERROR, STATUS_NO_DATA_YET, STATUS_OK
from paper_trading.scorecard import evaluate
from paper_trading.ticklog import TickLogEntry


def make_record(
    *, name="rehearsal-x", total_sold="0", uninvested_cash="1.75", attempted=6, rejected=0,
    reasons=None, status="COMPLETED",
) -> dict:
    return {
        "experiment_name": name,
        "status": status,
        "metrics": {
            "total_sold_capital": total_sold,
            "uninvested_cash_value": uninvested_cash,
            "attempted_orders": attempted,
            "rejected_orders": rejected,
            "rejections_by_reason": reasons or {},
        },
    }


def make_tick(status: str, at: int = 0, **kwargs) -> TickLogEntry:
    return TickLogEntry(at=at, status=status, **kwargs)


class NotYetEvaluatedTests(unittest.TestCase):
    def test_checks_that_need_a_run_wait_before_the_first_tick(self) -> None:
        card = evaluate([], rehearsal_record=None, benchmark_record=None)
        needs_a_run = {"only_buys", "cash_never_negative", "benchmark_available", "rejection_rate_is_low"}
        for check in card.checks:
            if check.id in needs_a_run:
                self.assertEqual(check.result, "NOT_YET_EVALUATED", check.id)
        self.assertFalse(card.fully_passing)

    def test_a_crash_free_start_with_no_ticks_is_a_real_pass_not_a_wait(self) -> None:
        # There is nothing to have crashed yet, which is a true, positive fact --
        # not the same thing as "not enough history to judge" (feed_reliable, below).
        card = evaluate([], rehearsal_record=None, benchmark_record=None)
        self.assertEqual(next(c for c in card.checks if c.id == "no_crashes").result, "PASS")

    def test_feed_reliability_waits_for_a_minimum_number_of_ticks(self) -> None:
        card = evaluate([make_tick(STATUS_OK)] * 3, rehearsal_record=None, benchmark_record=None)
        feed = next(c for c in card.checks if c.id == "feed_reliable")
        self.assertEqual(feed.result, "NOT_YET_EVALUATED")


class FeedReliabilityTests(unittest.TestCase):
    def test_an_occasional_failure_still_passes(self) -> None:
        ticks = [make_tick(STATUS_OK)] * 9 + [make_tick(STATUS_FEED_ERROR)]
        card = evaluate(ticks, rehearsal_record=None, benchmark_record=None)
        feed = next(c for c in card.checks if c.id == "feed_reliable")
        self.assertEqual(feed.result, "PASS")

    def test_frequent_failure_fails(self) -> None:
        ticks = [make_tick(STATUS_FEED_ERROR)] * 6 + [make_tick(STATUS_OK)] * 4
        card = evaluate(ticks, rehearsal_record=None, benchmark_record=None)
        feed = next(c for c in card.checks if c.id == "feed_reliable")
        self.assertEqual(feed.result, "FAIL")
        self.assertIn("6 of 10", feed.detail)


class CrashTests(unittest.TestCase):
    def test_no_failed_run_passes(self) -> None:
        ticks = [make_tick(STATUS_OK, rehearsal_status="COMPLETED", benchmark_status="COMPLETED")]
        card = evaluate(ticks, rehearsal_record=None, benchmark_record=None)
        self.assertEqual(next(c for c in card.checks if c.id == "no_crashes").result, "PASS")

    def test_a_failed_run_fails_the_check(self) -> None:
        ticks = [make_tick(STATUS_OK, rehearsal_status="FAILED", benchmark_status="COMPLETED")]
        card = evaluate(ticks, rehearsal_record=None, benchmark_record=None)
        self.assertEqual(next(c for c in card.checks if c.id == "no_crashes").result, "FAIL")


class InvariantTests(unittest.TestCase):
    def test_a_sale_would_fail_the_only_buys_check(self) -> None:
        rehearsal = make_record(total_sold="5.00")
        card = evaluate([], rehearsal_record=rehearsal, benchmark_record=make_record(name="b"))
        check = next(c for c in card.checks if c.id == "only_buys")
        self.assertEqual(check.result, "FAIL")
        self.assertIn("rehearsal-x", check.detail)

    def test_negative_cash_would_fail_its_check(self) -> None:
        rehearsal = make_record(uninvested_cash="-0.01")
        card = evaluate([], rehearsal_record=rehearsal, benchmark_record=make_record(name="b"))
        check = next(c for c in card.checks if c.id == "cash_never_negative")
        self.assertEqual(check.result, "FAIL")

    def test_ordinary_records_pass_both_invariants(self) -> None:
        card = evaluate([], rehearsal_record=make_record(), benchmark_record=make_record(name="b"))
        for check_id in ("only_buys", "cash_never_negative"):
            self.assertEqual(next(c for c in card.checks if c.id == check_id).result, "PASS")


class BenchmarkAvailableTests(unittest.TestCase):
    def test_a_rehearsal_run_with_no_benchmark_fails(self) -> None:
        card = evaluate([], rehearsal_record=make_record(), benchmark_record=None)
        self.assertEqual(next(c for c in card.checks if c.id == "benchmark_available").result, "FAIL")

    def test_both_present_passes(self) -> None:
        card = evaluate([], rehearsal_record=make_record(), benchmark_record=make_record(name="b"))
        self.assertEqual(next(c for c in card.checks if c.id == "benchmark_available").result, "PASS")


class RejectionRateTests(unittest.TestCase):
    def test_a_low_rejection_rate_passes(self) -> None:
        rehearsal = make_record(attempted=10, rejected=1, reasons={"NOTIONAL_BELOW_MIN": 1})
        card = evaluate([], rehearsal_record=rehearsal, benchmark_record=None)
        check = next(c for c in card.checks if c.id == "rejection_rate_is_low")
        self.assertEqual(check.result, "PASS")
        self.assertIn("NOTIONAL_BELOW_MIN", check.detail)

    def test_a_high_rejection_rate_fails_and_names_the_reason(self) -> None:
        rehearsal = make_record(attempted=10, rejected=9, reasons={"NOTIONAL_BELOW_MIN": 9})
        card = evaluate([], rehearsal_record=rehearsal, benchmark_record=None)
        check = next(c for c in card.checks if c.id == "rejection_rate_is_low")
        self.assertEqual(check.result, "FAIL")
        self.assertIn("NOTIONAL_BELOW_MIN=9", check.detail)


class OverallPassTests(unittest.TestCase):
    def test_fully_passing_only_once_every_check_is_a_real_pass(self) -> None:
        ticks = [make_tick(STATUS_OK, rehearsal_status="COMPLETED", benchmark_status="COMPLETED")] * 5
        good = evaluate(ticks, rehearsal_record=make_record(), benchmark_record=make_record(name="b"))
        self.assertTrue(good.fully_passing)

        bad = evaluate(ticks, rehearsal_record=make_record(total_sold="1"), benchmark_record=make_record(name="b"))
        self.assertFalse(bad.fully_passing)

    def test_a_pending_check_counts_against_fully_passing_not_for_it(self) -> None:
        # Zero ticks: only no_crashes is a real verdict, and it is vacuously true.
        # fully_passing must not read that as "everything is fine".
        card = evaluate([], rehearsal_record=None, benchmark_record=None)
        self.assertFalse(card.fully_passing)

    def test_to_dict_is_json_safe(self) -> None:
        import json

        card = evaluate([make_tick(STATUS_NO_DATA_YET)], rehearsal_record=None, benchmark_record=None)
        json.dumps(card.to_dict())


if __name__ == "__main__":
    unittest.main()
