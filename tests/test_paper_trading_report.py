"""The weekly write-up: what it says, and what it deliberately never claims."""

from __future__ import annotations

import unittest

from paper_trading.report import NO_EDGE_REMINDER, build_report
from paper_trading.runner import STATUS_NO_DATA_YET, STATUS_OK
from paper_trading.scorecard import Scorecard, ScorecardCheck
from paper_trading.ticklog import TickLogEntry

FORBIDDEN_WORDS = ("beat", "beats", "better", "worse", "winner", "recommend", "should buy", "guaranteed")


def make_record(name: str, *, ending="204.57", capital_in="200.00", net_return="0.0229",
                drawdown="0.0033", cash="1.75", cost_drag="1.34", accepted=12, rejected=0,
                end_time=1_700_000_000_000) -> dict:
    return {
        "experiment_name": name,
        "date_range": {"end_time": end_time},
        "metrics": {
            "total_capital_in": capital_in,
            "ending_portfolio_value": ending,
            "net_return": net_return,
            "max_drawdown": drawdown,
            "uninvested_cash_value": cash,
            "total_cost_drag": cost_drag,
            "accepted_orders": accepted,
            "rejected_orders": rejected,
        },
    }


EMPTY_SCORECARD = Scorecard(())


class NoTicksTests(unittest.TestCase):
    def test_a_plan_that_has_never_ticked_says_so_plainly(self) -> None:
        text = build_report("my-plan", rehearsal_record=None, benchmark_record=None, scorecard=EMPTY_SCORECARD, ticks=())
        self.assertIn("No ticks have run yet", text)
        self.assertNotIn("$", text)


class NoCompletedRunTests(unittest.TestCase):
    def test_ticks_with_no_run_yet_still_produce_a_report(self) -> None:
        ticks = (TickLogEntry(at=1, status=STATUS_NO_DATA_YET),)
        text = build_report("my-plan", rehearsal_record=None, benchmark_record=None, scorecard=EMPTY_SCORECARD, ticks=ticks)
        self.assertIn("Neither track has completed a run yet", text)
        self.assertIn(NO_EDGE_REMINDER, text)


class ComparisonTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rehearsal = make_record("r")
        self.benchmark = make_record("b", ending="200.00", net_return="0", drawdown="0", cash="200.00", cost_drag="0", accepted=0)
        self.ticks = (TickLogEntry(at=1, status=STATUS_OK, rehearsal_run_id="x", rehearsal_status="COMPLETED",
                                   benchmark_run_id="y", benchmark_status="COMPLETED"),)
        self.card = Scorecard((ScorecardCheck("no_crashes", "PASS", "no run has ever failed"),))

    def build(self):
        return build_report("my-plan", rehearsal_record=self.rehearsal, benchmark_record=self.benchmark,
                            scorecard=self.card, ticks=self.ticks)

    def test_both_tracks_dollar_figures_appear(self) -> None:
        text = self.build()
        self.assertIn("$204.57", text)
        self.assertIn("$200.00", text)
        self.assertIn("2.29%", text)

    def test_the_scorecard_is_printed_with_its_result_and_detail(self) -> None:
        text = self.build()
        self.assertIn("no_crashes", text)
        self.assertIn("no run has ever failed", text)
        self.assertIn("PASS", text)

    def test_columns_do_not_run_together(self) -> None:
        text = self.build()
        # a rendered cell (ending in a digit, % or a two-decimal amount) must never
        # butt directly against the next column's $ or digit with no space between
        self.assertNotRegex(text, r"[%$][\d,.]+[%$]")
        self.assertNotRegex(text, r"\d%\d")

    def test_the_no_edge_reminder_always_appears_and_says_no_verdict(self) -> None:
        text = self.build()
        self.assertIn(NO_EDGE_REMINDER, text)
        self.assertIn("cannot show", NO_EDGE_REMINDER)

    def test_the_report_never_declares_a_winner(self) -> None:
        # NO_EDGE_REMINDER itself says "cannot show ... beats" -- that is the
        # disclaimer, not a verdict. Check everything else in the report instead.
        text = self.build().replace(NO_EDGE_REMINDER, "").lower()
        for word in FORBIDDEN_WORDS:
            self.assertNotIn(word, text, word)

    def test_a_missing_track_shows_only_the_available_column(self) -> None:
        text = build_report("my-plan", rehearsal_record=self.rehearsal, benchmark_record=None,
                            scorecard=self.card, ticks=self.ticks)
        header_line = next(line for line in text.splitlines() if "rehearsal" in line)
        self.assertNotIn("benchmark", header_line)


if __name__ == "__main__":
    unittest.main()
