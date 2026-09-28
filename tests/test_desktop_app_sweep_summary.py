"""desktop_app.sweep_summary -- buckets and the at-a-glance numbers. Pure, no engine."""

from __future__ import annotations

import unittest
from decimal import Decimal

from desktop_app.plan_builder import Schedule
from desktop_app.sweep import SweepResult, WindowOutcome
from crypto_research.timeutil import parse_time

from desktop_app.sweep_summary import Bucket, bucket_for, invested_nothing, summarize

D = Decimal


def _outcome(
    ending: str, *, start: int = 0, capital: str = "100", drawdown: str = "0.1", rejected: int = 0
) -> WindowOutcome:
    return WindowOutcome(start, D(capital), D(ending), D(drawdown), rejected)


def _result(*outcomes: WindowOutcome, skipped: int = 0) -> SweepResult:
    return SweepResult(
        amount=D("100"),
        symbols=("BTCUSDT",),
        schedule=Schedule.ALL_AT_ONCE,
        hold_months=12,
        outcomes=tuple(outcomes),
        skipped=skipped,
    )


class BucketTests(unittest.TestCase):
    def test_the_boundaries(self) -> None:
        cases = {
            "200": Bucket.DOUBLED,  # exactly double counts as doubled
            "199.99": Bucket.GAINED,
            "100": Bucket.GAINED,  # break-even is not a loss
            "99.99": Bucket.LOST_SOME,
            "50": Bucket.LOST_SOME,  # exactly half is not "lost over half"
            "49.99": Bucket.LOST_MOST,
            "0": Bucket.LOST_MOST,
        }
        for ending, expected in cases.items():
            with self.subTest(ending=ending):
                self.assertIs(bucket_for(_outcome(ending)), expected)

    def test_nothing_put_in_means_nothing_lost(self) -> None:
        self.assertIs(bucket_for(_outcome("0", capital="0")), Bucket.GAINED)


class SummarizeTests(unittest.TestCase):
    def test_nothing_to_summarise_is_none(self) -> None:
        self.assertIsNone(summarize(_result()))

    def test_worst_best_and_typical(self) -> None:
        summary = summarize(
            _result(
                _outcome("400", start=1),
                _outcome("50", start=2),
                _outcome("100", start=3),
                _outcome("150", start=4),
                _outcome("90", start=5),
            )
        )
        self.assertEqual(summary.worst.ending_value, D("50"))
        self.assertEqual(summary.worst.start_ms, 2)
        self.assertEqual(summary.best.ending_value, D("400"))
        self.assertEqual(summary.typical_ending, D("100"))
        self.assertEqual(summary.tested, 5)
        self.assertEqual((summary.first_start_ms, summary.last_start_ms), (1, 5))

    def test_typical_of_an_even_count_is_the_mean_of_the_two_middle_results(self) -> None:
        summary = summarize(_result(_outcome("100"), _outcome("200"), _outcome("900"), _outcome("40")))
        self.assertEqual(summary.typical_ending, D("150"))

    def test_counts_use_the_same_buckets_as_the_map(self) -> None:
        outcomes = [
            _outcome("400"),  # doubled
            _outcome("250"),  # doubled
            _outcome("120"),  # gained
            _outcome("80"),  # lost some
            _outcome("30"),  # lost most
        ]
        summary = summarize(_result(*outcomes))
        self.assertEqual(summary.doubled_or_better, 2)
        self.assertEqual(summary.lost_money, 2)
        buckets = [bucket_for(o) for o in outcomes]
        self.assertEqual(
            summary.lost_money, sum(b in (Bucket.LOST_SOME, Bucket.LOST_MOST) for b in buckets)
        )

    def test_a_fall_of_exactly_half_counts_as_halved(self) -> None:
        summary = summarize(
            _result(_outcome("100", drawdown="0.5"), _outcome("100", drawdown="0.4999"))
        )
        self.assertEqual(summary.halved_at_some_point, 1)

    def test_ties_resolve_to_the_earliest_start(self) -> None:
        summary = summarize(_result(_outcome("50", start=9), _outcome("50", start=3), _outcome("70", start=5)))
        self.assertEqual(summary.worst.start_ms, 3)
        best_tie = summarize(_result(_outcome("90", start=9), _outcome("90", start=3)))
        self.assertEqual(best_tie.best.start_ms, 3)

    def test_rejections_and_skips_are_carried_through(self) -> None:
        summary = summarize(
            _result(_outcome("100", rejected=3), _outcome("100"), _outcome("100", rejected=1), skipped=7)
        )
        self.assertEqual(summary.windows_with_rejections, 2)
        self.assertEqual(summary.skipped, 7)


class SpanTests(unittest.TestCase):
    def test_span_is_the_calendar_months_between_first_and_last_start(self) -> None:
        jan_2021 = parse_time("2021-01-01T00:00:00Z")
        mar_2023 = parse_time("2023-03-01T00:00:00Z")
        summary = summarize(_result(_outcome("100", start=jan_2021), _outcome("100", start=mar_2023)))
        self.assertEqual(summary.span_months, 26)

    def test_a_single_month_has_a_span_of_zero(self) -> None:
        self.assertEqual(summarize(_result(_outcome("100", start=parse_time("2021-01-01T00:00:00Z")))).span_months, 0)


class InvestedNothingTests(unittest.TestCase):
    def test_every_try_flat_with_refused_buys_means_nothing_was_invested(self) -> None:
        result = _result(_outcome("100", rejected=12), _outcome("100", rejected=12))
        self.assertTrue(invested_nothing(result))

    def test_a_try_that_moved_means_money_was_invested(self) -> None:
        result = _result(_outcome("100", rejected=12), _outcome("104", rejected=1))
        self.assertFalse(invested_nothing(result))

    def test_flat_with_no_refusals_is_just_a_flat_market_not_a_failure(self) -> None:
        self.assertFalse(invested_nothing(_result(_outcome("100"), _outcome("100"))))

    def test_no_tries_at_all_is_not_reported_as_nothing_invested(self) -> None:
        self.assertFalse(invested_nothing(_result()))


if __name__ == "__main__":
    unittest.main()
