"""desktop_app.metrics_presenter -- the plain-language wording of a sweep."""

from __future__ import annotations

import unittest
from decimal import Decimal

from crypto_research.timeutil import add_months, parse_time

from desktop_app import glossary, metrics_presenter
from desktop_app.plan_builder import Schedule
from desktop_app.sweep import SweepResult, WindowOutcome
from desktop_app.sweep_summary import summarize

D = Decimal
JAN_2021 = parse_time("2021-01-01T00:00:00Z")
FEB_2021 = parse_time("2021-02-01T00:00:00Z")
MAR_2021 = parse_time("2021-03-01T00:00:00Z")


def _outcome(start_ms: int, ending: str, *, drawdown: str = "0.1", rejected: int = 0) -> WindowOutcome:
    return WindowOutcome(start_ms, D("200"), D(ending), D(drawdown), rejected)


def _result(*outcomes: WindowOutcome, amount: str = "200", skipped: int = 0) -> SweepResult:
    return SweepResult(
        amount=D(amount),
        symbols=("BTCUSDT", "ETHUSDT"),
        schedule=Schedule.SPREAD_OVER_WEEKS,
        hold_months=12,
        outcomes=tuple(outcomes),
        skipped=skipped,
    )


RESULT = _result(
    _outcome(JAN_2021, "500", drawdown="0.6"),  # doubled, and fell by half at some point
    _outcome(FEB_2021, "250"),  # gained
    _outcome(MAR_2021, "120"),  # lost some
)


class FormatMoneyTests(unittest.TestCase):
    def test_cents_and_whole_dollars(self) -> None:
        self.assertEqual(metrics_presenter.format_money(D("1234.5")), "$1,234.50")
        self.assertEqual(metrics_presenter.format_money(D("1234.5"), cents=False), "$1,235")

    def test_negative_and_zero(self) -> None:
        self.assertEqual(metrics_presenter.format_money(D("-5")), "-$5.00")
        self.assertEqual(metrics_presenter.format_money(D("0")), "$0.00")

    def test_a_tiny_negative_never_prints_as_minus_zero(self) -> None:
        self.assertEqual(metrics_presenter.format_money(D("-0.001")), "$0.00")

    def test_rounds_half_up(self) -> None:
        self.assertEqual(metrics_presenter.format_money(D("0.125")), "$0.13")


class FormatPercentTests(unittest.TestCase):
    def test_always_carries_a_sign(self) -> None:
        self.assertEqual(metrics_presenter.format_percent(D("0.25")), "+25%")
        self.assertEqual(metrics_presenter.format_percent(D("-0.28")), "-28%")
        self.assertEqual(metrics_presenter.format_percent(D("0")), "+0%")

    def test_large_gains_get_thousands_separators(self) -> None:
        self.assertEqual(metrics_presenter.format_percent(D("24.4")), "+2,440%")


class WordingTests(unittest.TestCase):
    def test_month_label(self) -> None:
        self.assertEqual(metrics_presenter.month_label(JAN_2021), "Jan 2021")

    def test_coin_names_read_as_a_sentence(self) -> None:
        names = metrics_presenter.coin_names
        self.assertEqual(names(("BTCUSDT",)), "Bitcoin")
        self.assertEqual(names(("BTCUSDT", "ETHUSDT")), "Bitcoin and Ethereum")
        self.assertEqual(names(("BTCUSDT", "ETHUSDT", "SOLUSDT")), "Bitcoin, Ethereum and Solana")

    def test_an_unknown_coin_falls_back_to_its_code_not_a_crash(self) -> None:
        self.assertEqual(metrics_presenter.coin_names(("SHIBUSDT",)), "SHIB")

    def test_title_and_description(self) -> None:
        self.assertEqual(
            metrics_presenter.title(RESULT), "$200 in Bitcoin and Ethereum, spread over 12 weeks"
        )
        self.assertEqual(
            metrics_presenter.describe(RESULT),
            "$200  |  Bitcoin and Ethereum  |  spread over 12 weeks  |  1 year",
        )

    def test_a_small_stake_keeps_its_cents(self) -> None:
        small = _result(_outcome(JAN_2021, "20"), amount="19.5")
        self.assertTrue(metrics_presenter.title(small).startswith("$19.50 in"))

    def test_subtitle_names_the_range_and_count(self) -> None:
        summary = summarize(RESULT)
        text = metrics_presenter.subtitle(RESULT, summary)
        self.assertIn("Jan 2021", text)
        self.assertIn("Mar 2021", text)
        self.assertIn("3 starting months", text)
        self.assertIn("1 year", text)

    def test_when_label_reads_like_a_time_in_the_persons_own_zone(self) -> None:
        text = metrics_presenter.when_label("2026-09-27T19:12:00Z")
        self.assertRegex(text, r"^[A-Z][a-z]{2} \d{1,2}, 202\d, \d{1,2}:\d{2} (AM|PM)$")


class RowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = {row.label: row for row in metrics_presenter.rows(summarize(RESULT))}

    def test_worst_and_best_name_their_starting_month(self) -> None:
        self.assertEqual(self.rows["Worst starting month"].value, "$120  (started Mar 2021)")
        self.assertEqual(self.rows["Best starting month"].value, "$500  (started Jan 2021)")

    def test_typical_is_the_middle_result(self) -> None:
        self.assertEqual(self.rows["Typical result"].value, "$250")

    def test_counts_read_as_x_of_n(self) -> None:
        self.assertEqual(self.rows["Lost money"].value, "1 of 3 starting months")
        self.assertEqual(self.rows["Doubled your money or better"].value, "1 of 3")
        self.assertEqual(self.rows["Fell by half or more at some point"].value, "1 of 3")

    def test_the_cash_comparison_uses_the_amount_typed(self) -> None:
        self.assertEqual(self.rows["Just keeping it as cash"].value, "$200 every time")

    def test_every_row_term_is_declared_and_defined(self) -> None:
        declared = set(metrics_presenter.GLOSSARY_TERMS_USED)
        for row in self.rows.values():
            self.assertIn(row.term, declared)
            self.assertIn(row.term, glossary.DEFINITIONS)
        self.assertEqual({row.term for row in self.rows.values()}, declared)


def _spread_over(*months: int) -> SweepResult:
    """A result whose starting months are the given number of months after Jan 2021."""
    return _result(*[_outcome(add_months(JAN_2021, m), "250") for m in months])


class NotesTests(unittest.TestCase):
    def test_the_caution_is_always_present_and_names_the_survivor_problem(self) -> None:
        lines = metrics_presenter.notes(summarize(_spread_over(0, 12, 30)))
        self.assertEqual(len(lines), 2)  # the caution, and what is and isn't counted
        self.assertIn("when you started", lines[0])
        self.assertIn("still exist today", lines[0])
        self.assertIn("Past results don't predict", lines[0])

    def test_it_says_what_is_counted_and_what_is_not(self) -> None:
        text = metrics_presenter.notes(summarize(_spread_over(0, 12, 30)))[1]
        self.assertIn("USDT", text)
        self.assertIn(f"about {metrics_presenter.ASSUMED_COST_PERCENT}", text)
        self.assertIn("Taxes", text)

    def test_the_quoted_cost_is_what_the_engine_really_charges(self) -> None:
        # The note says "about 0.7%". If the engine's default costs change, this
        # fails so the sentence gets updated rather than quietly becoming untrue.
        from crypto_research.config import parse_config

        from desktop_app.plan_builder import PlanRequest, build_config_payload

        config = parse_config(
            build_config_payload(
                PlanRequest(D("200"), ("BTCUSDT",), Schedule.ALL_AT_ONCE, "2024-01-01", "2025-01-01")
            )
        )
        costs = config.backtest.costs
        per_buy = (
            costs.fees.schedule.taker_rate
            + (costs.spread.default_bps + costs.slippage.bps) / D(10000)
        )
        self.assertEqual(f"{per_buy * 100:.1f}%", metrics_presenter.ASSUMED_COST_PERCENT)

    def test_a_narrow_slice_of_history_is_disclosed_with_its_dates(self) -> None:
        lines = metrics_presenter.notes(summarize(RESULT))  # Jan-Mar 2021: 2 months
        narrow = [line for line in lines if "narrow slice of history" in line]
        self.assertEqual(len(narrow), 1)
        self.assertIn("Jan 2021", narrow[0])
        self.assertIn("Mar 2021", narrow[0])

    def test_the_narrow_slice_boundary_is_two_years(self) -> None:
        narrow = lambda *m: any(  # noqa: E731
            "narrow slice" in line for line in metrics_presenter.notes(summarize(_spread_over(*m)))
        )
        self.assertTrue(narrow(0, 23))
        self.assertFalse(narrow(0, 24))

    def test_skipped_months_are_disclosed(self) -> None:
        lines = metrics_presenter.notes(summarize(_result(_outcome(JAN_2021, "250"), skipped=3)))
        self.assertTrue(any("3 starting month(s) weren't tried" in line for line in lines))

    def test_rejected_buys_are_disclosed(self) -> None:
        lines = metrics_presenter.notes(
            summarize(_result(_outcome(JAN_2021, "250", rejected=2), _outcome(FEB_2021, "250")))
        )
        self.assertTrue(any("In 1 of 2 tries" in line and "too small" in line for line in lines))


class CellTooltipTests(unittest.TestCase):
    def test_says_when_how_much_and_the_change(self) -> None:
        text = metrics_presenter.cell_tooltip(_outcome(JAN_2021, "249"))
        self.assertEqual(text, "Started Jan 2021: $200 ended at $249 (+25%)")

    def test_a_loss_is_signed(self) -> None:
        text = metrics_presenter.cell_tooltip(_outcome(JAN_2021, "143"))
        self.assertIn("(-29%)", text)


def _summary(*endings: str, start: int = JAN_2021, amount: str = "200", drawdown: str = "0.1"):
    outcomes = tuple(
        WindowOutcome(add_months(start, i), D(amount), D(e), D(drawdown), 0)
        for i, e in enumerate(endings)
    )
    return summarize(_result(*outcomes, amount=amount))


class ComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = _summary("500", "250", "120")  # worst 120 (Mar), best 500 (Jan)
        self.fund = _summary("230", "240", "210")
        self.savings = _summary("205", "206", "207")
        self.table = metrics_presenter.comparison(self.plan, self.fund, self.savings)

    def _row(self, label: str):
        return next(row for row in self.table.rows if row.label == label)

    def test_three_columns_in_a_fixed_order(self) -> None:
        self.assertEqual(
            [c.title for c in self.table.columns],
            ["Your plan", "Stock market fund", "Savings account"],
        )

    def test_six_rows_each_with_a_cell_per_column(self) -> None:
        self.assertEqual(
            [r.label for r in self.table.rows],
            [
                "Worst starting month", "Typical result", "Best starting month",
                "Lost money", "Doubled or better", "Fell by half or more",
            ],
        )
        for row in self.table.rows:
            self.assertEqual(len(row.cells), 3)

    def test_the_plans_own_extremes_name_their_month_the_yardsticks_put_it_in_a_tooltip(self) -> None:
        worst = self._row("Worst starting month").cells
        self.assertEqual(worst[0].text, "$120 (Mar 2021)")
        self.assertIsNone(worst[0].tooltip)
        self.assertEqual((worst[1].text, worst[1].tooltip), ("$210", "Started Mar 2021"))
        self.assertEqual((worst[2].text, worst[2].tooltip), ("$205", "Started Jan 2021"))
        best = self._row("Best starting month").cells
        self.assertEqual(best[0].text, "$500 (Jan 2021)")
        self.assertEqual(best[1].text, "$240")

    def test_typical_and_counts(self) -> None:
        self.assertEqual(
            [c.text for c in self._row("Typical result").cells], ["$250", "$230", "$206"]
        )
        self.assertEqual(
            [c.text for c in self._row("Lost money").cells], ["1 of 3", "0 of 3", "0 of 3"]
        )
        self.assertEqual(
            [c.text for c in self._row("Doubled or better").cells], ["1 of 3", "0 of 3", "0 of 3"]
        )

    def test_each_column_counts_out_of_its_own_number_of_months(self) -> None:
        shorter = _summary("230", "240")  # the fund only covered two of the three months
        table = metrics_presenter.comparison(self.plan, shorter, self.savings)
        lost = next(r for r in table.rows if r.label == "Lost money")
        self.assertEqual([c.text for c in lost.cells], ["1 of 3", "0 of 2", "0 of 3"])

    def test_a_missing_yardstick_simply_has_no_column(self) -> None:
        only_fund = metrics_presenter.comparison(self.plan, self.fund, None)
        self.assertEqual([c.title for c in only_fund.columns], ["Your plan", "Stock market fund"])
        for row in only_fund.rows:
            self.assertEqual(len(row.cells), 2)
        alone = metrics_presenter.comparison(self.plan, None, None)
        self.assertEqual([c.title for c in alone.columns], ["Your plan"])

    def test_a_small_stake_keeps_its_cents_in_every_column(self) -> None:
        small_plan = _summary("25", "26", "27", amount="20")
        small_fund = _summary("21", "22", "23", amount="20")
        table = metrics_presenter.comparison(small_plan, small_fund, None)
        typical = next(r for r in table.rows if r.label == "Typical result")
        self.assertEqual([c.text for c in typical.cells], ["$26.00", "$22.00"])

    def test_every_term_used_is_defined(self) -> None:
        terms = {row.term for row in self.table.rows} | {c.term for c in self.table.columns}
        self.assertEqual(terms, set(metrics_presenter.COMPARISON_TERMS_USED))
        for term in terms:
            self.assertIn(term, glossary.DEFINITIONS)


class ComparisonNotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.summary = summarize(_spread_over(0, 12, 30))

    def test_no_comparison_no_extra_note(self) -> None:
        self.assertEqual(len(metrics_presenter.notes(self.summary)), 2)

    def test_a_comparison_explains_itself_and_warns_about_the_missing_crash(self) -> None:
        lines = metrics_presenter.notes(self.summary, compared=True)
        self.assertEqual(len(lines), 3)
        text = lines[2]
        self.assertIn("same starting months", text)
        self.assertIn("same days", text)
        self.assertIn("no trading costs or taxes", text)
        self.assertIn("2000-02", text)
        self.assertIn("2008-09", text)
        self.assertIn("steadier", text)

    def test_the_yardsticks_covering_fewer_months_is_disclosed(self) -> None:
        lines = metrics_presenter.notes(self.summary, compared=True, compared_months=2)
        self.assertTrue(any("cover 2 of the 3 starting months" in line for line in lines))

    def test_full_coverage_says_nothing_about_coverage(self) -> None:
        lines = metrics_presenter.notes(self.summary, compared=True, compared_months=3)
        self.assertFalse(any("cover" in line and "of the" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
