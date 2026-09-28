"""The starting-month map and the shared result view -- need a real (never-shown) Tk root."""

from __future__ import annotations

import tkinter as tk
import unittest
from decimal import Decimal

from crypto_research.timeutil import add_months, parse_time

from desktop_app.plan_builder import Schedule
from desktop_app.sweep import SweepResult, WindowOutcome
from desktop_app.widgets.start_map import StartMap
from desktop_app.widgets.sweep_result import SweepResultView

from .tk_helpers import TkTestCase, show_offscreen

D = Decimal
SYMBOLS = {"++", "+", "-", "--"}


def _outcome(months_after_2021: int, ending: str) -> WindowOutcome:
    start = add_months(parse_time("2021-01-01T00:00:00Z"), months_after_2021)
    return WindowOutcome(start, D("200"), D(ending), D("0.1"), 0)


def _result(*outcomes: WindowOutcome) -> SweepResult:
    return SweepResult(
        amount=D("200"),
        symbols=("BTCUSDT",),
        schedule=Schedule.ALL_AT_ONCE,
        hold_months=12,
        outcomes=tuple(outcomes),
        skipped=0,
    )


def _squares(widget: tk.Widget) -> list[tk.Label]:
    found = []
    stack = list(widget.winfo_children())
    while stack:
        child = stack.pop()
        if isinstance(child, tk.Label) and child.cget("text") in SYMBOLS:
            found.append(child)
        stack.extend(child.winfo_children())
    return found


class StartMapTests(TkTestCase):
    def test_one_coloured_square_per_starting_month_plus_the_legend(self) -> None:
        self.start_map = start_map = StartMap(self.root)
        start_map.render([_outcome(0, "500"), _outcome(1, "250"), _outcome(2, "150"), _outcome(3, "80")])
        # 4 outcomes + 4 legend swatches
        self.assertEqual(len(_squares(start_map)), 8)

    def test_each_kind_of_result_gets_its_own_symbol(self) -> None:
        self.start_map = start_map = StartMap(self.root)
        start_map.render([_outcome(0, "500"), _outcome(1, "250"), _outcome(2, "150"), _outcome(3, "80")])
        grid_symbols = [
            w.cget("text")
            for w in start_map.grid_frame.grid_slaves()
            if isinstance(w, tk.Label) and w.cget("text") in SYMBOLS
        ]
        # $500 doubled, $250 gained, $150 lost some (of a $200 stake), $80 lost over half.
        self.assertEqual(sorted(grid_symbols), sorted(["++", "+", "-", "--"]))

    def test_a_result_below_half_gets_the_strongest_loss_symbol(self) -> None:
        self.start_map = start_map = StartMap(self.root)
        start_map.render([_outcome(0, "60")])
        symbols = [w.cget("text") for w in start_map.grid_frame.grid_slaves() if isinstance(w, tk.Label)]
        self.assertIn("--", symbols)

    def test_rendering_again_replaces_rather_than_stacks(self) -> None:
        self.start_map = start_map = StartMap(self.root)
        start_map.render([_outcome(0, "500"), _outcome(1, "250")])
        first = len(start_map.grid_frame.winfo_children())
        legend_first = len(start_map.legend_frame.winfo_children())
        start_map.render([_outcome(0, "500"), _outcome(1, "250")])
        self.assertEqual(len(start_map.grid_frame.winfo_children()), first)
        self.assertEqual(len(start_map.legend_frame.winfo_children()), legend_first)

    def test_nothing_to_draw_is_fine(self) -> None:
        self.start_map = start_map = StartMap(self.root)
        start_map.render([])
        self.assertEqual(start_map.grid_frame.winfo_children(), [])
        self.assertEqual(start_map.legend_frame.winfo_children(), [])

    def test_a_result_that_spans_years_gets_a_row_per_year(self) -> None:
        self.start_map = start_map = StartMap(self.root)
        start_map.render([_outcome(0, "250"), _outcome(30, "250")])  # Jan 2021 and Jul 2023
        year_labels = [
            w.cget("text")
            for w in start_map.grid_frame.grid_slaves()
            if w.winfo_class() == "TLabel" and str(w.cget("text")).isdigit()
        ]
        self.assertEqual(sorted(year_labels), ["2021", "2022", "2023"])


class SweepResultViewTests(TkTestCase):
    def test_renders_a_result_and_can_be_cleared_and_rendered_again(self) -> None:
        self.view = view = SweepResultView(self.root)
        result = _result(*[_outcome(i, str(100 + 20 * i)) for i in range(8)])
        view.render(result)
        self.root.update_idletasks()
        self.assertTrue(view._title.winfo_manager())
        view.clear()
        self.assertFalse(view._title.winfo_manager())
        view.render(result)
        self.assertTrue(view._title.winfo_manager())

    def test_an_empty_result_shows_nothing(self) -> None:
        self.view = view = SweepResultView(self.root)
        view.render(_result())
        self.assertFalse(view._title.winfo_manager())


class ComparisonViewTests(TkTestCase):
    _DAY = 24 * 60 * 60 * 1000

    def _reference(self, days: int = 1500):
        from desktop_app import alternatives

        base = parse_time("2020-06-01T00:00:00Z")
        prices = {base + i * self._DAY: 100.0 + i * 0.1 for i in range(days)}
        yields = {base + i * self._DAY: 4.0 for i in range(days)}
        return alternatives.build_reference(prices, yields)

    def _result(self, n: int = 8) -> SweepResult:
        return _result(*[_outcome(i, str(100 + 20 * i)) for i in range(n)])

    def test_with_reference_data_the_table_becomes_a_side_by_side_comparison(self) -> None:
        self.view = view = SweepResultView(self.root)
        view.render(self._result(), self._reference())
        self.root.update_idletasks()
        self.assertTrue(view.showing_comparison)
        self.assertFalse(view._table.winfo_manager())
        headings = [w.cget("text") for w in view._comparison.grid_slaves(row=0)]
        self.assertCountEqual(headings, ["Your plan", "Stock market fund", "Savings account"])

    def test_without_reference_data_the_plain_table_is_shown(self) -> None:
        self.view = view = SweepResultView(self.root)
        view.render(self._result(), None)
        self.assertFalse(view.showing_comparison)
        self.assertTrue(view._table.winfo_manager())

    def test_reference_data_that_covers_none_of_the_months_falls_back_to_the_plain_table(self) -> None:
        from desktop_app import alternatives

        far_future = parse_time("2040-01-01T00:00:00Z")
        ref = alternatives.build_reference(
            {far_future + i * self._DAY: 100.0 for i in range(200)},
            {far_future + i * self._DAY: 4.0 for i in range(200)},
        )
        self.view = view = SweepResultView(self.root)
        view.render(self._result(), ref)
        self.assertFalse(view.showing_comparison)
        self.assertTrue(view._table.winfo_manager())

    def test_switching_between_the_two_leaves_only_one_table_showing(self) -> None:
        self.view = view = SweepResultView(self.root)
        view.render(self._result(), self._reference())
        view.render(self._result(), None)
        self.assertFalse(view._comparison.winfo_manager())
        self.assertTrue(view._table.winfo_manager())
        view.render(self._result(), self._reference())
        self.assertFalse(view._table.winfo_manager())
        self.assertTrue(view._comparison.winfo_manager())

    def test_the_comparison_adds_its_own_caution_to_the_notes(self) -> None:
        self.view = view = SweepResultView(self.root)
        view.render(self._result(), self._reference())
        self.assertIn("2008-09", view._notes.cget("text"))
        view.render(self._result(), None)
        self.assertNotIn("2008-09", view._notes.cget("text"))

    def test_every_label_and_every_yardstick_extreme_explains_itself_on_hover(self) -> None:
        self.view = view = SweepResultView(self.root)
        view.render(self._result(), self._reference())
        labels = [w for w in view._comparison.winfo_children() if w.winfo_class() == "TLabel"]
        self.assertGreater(len(labels), 20)
        row_labels = [w for w in labels if int(w.grid_info()["column"]) == 0]
        headings = [w for w in labels if int(w.grid_info()["row"]) == 0]
        for widget in row_labels + headings:
            self.assertTrue(widget.bind("<Enter>"), widget.cget("text"))
        # The worst and best cells of the two yardstick columns carry their month in a tooltip
        # (grid rows: 0 headings, 1 separator, 2 worst, 3 typical, 4 best, ...).
        yardstick_extremes = [
            w for w in labels
            if int(w.grid_info()["column"]) >= 2 and int(w.grid_info()["row"]) in (2, 4)
        ]
        self.assertEqual(len(yardstick_extremes), 4)
        for widget in yardstick_extremes:
            self.assertTrue(widget.bind("<Enter>"), widget.cget("text"))


class ResultLayoutTests(TkTestCase):
    """The map goes beside the table when both fit, and below it when they don't."""

    _DAY = 24 * 60 * 60 * 1000

    def _reference(self):
        from desktop_app import alternatives

        base = parse_time("2020-06-01T00:00:00Z")
        return alternatives.build_reference(
            {base + i * self._DAY: 100.0 + i * 0.1 for i in range(1500)},
            {base + i * self._DAY: 4.0 for i in range(1500)},
        )

    def _show(self, width: int) -> None:
        from tkinter import ttk

        self.container = ttk.Frame(self.root, width=width, height=700)
        self.container.pack_propagate(False)
        self.container.pack(anchor="nw")
        self.view = SweepResultView(self.container)
        self.view.pack(anchor="nw")
        show_offscreen(self.root, width + 20, 720)
        self.view.render(_result(*[_outcome(i, str(100 + 20 * i)) for i in range(8)]), self._reference())
        self.root.update()
        self.root.update()

    def test_with_plenty_of_room_the_map_sits_beside_the_table(self) -> None:
        self._show(1500)
        self.assertFalse(self.view.stacked)
        left, map_column = self.view._left, self.view._map_column
        self.assertGreaterEqual(map_column.winfo_x(), left.winfo_x() + left.winfo_width())
        self.assertLess(abs(map_column.winfo_y() - left.winfo_y()), 5)

    def test_without_room_the_map_drops_below_the_table_instead_of_being_cut_off(self) -> None:
        self._show(700)
        self.assertTrue(self.view.stacked)
        left, map_column = self.view._left, self.view._map_column
        self.assertGreaterEqual(map_column.winfo_y(), left.winfo_y() + left.winfo_height())

    def test_widening_the_space_puts_them_side_by_side_again(self) -> None:
        self._show(700)
        self.assertTrue(self.view.stacked)
        self.container.configure(width=1500)
        show_offscreen(self.root, 1520, 720)
        self.assertFalse(self.view.stacked)

    def test_narrowing_the_space_stacks_them(self) -> None:
        self._show(1500)
        self.assertFalse(self.view.stacked)
        self.container.configure(width=700)
        show_offscreen(self.root, 720, 720)
        self.assertTrue(self.view.stacked)

    def test_the_default_window_has_room_for_both_side_by_side(self) -> None:
        # The comparison table plus the map plus the gap must fit the space the app
        # gives them at its default and minimum sizes: 980 wide, less 36 of margin.
        self._show(944)
        self.assertFalse(self.view.stacked, "the map would have to drop below at the minimum window width")

    def test_it_never_relayouts_forever(self) -> None:
        self._show(900)
        state = self.view.stacked
        for _ in range(5):
            self.root.update()
        self.assertEqual(self.view.stacked, state)


if __name__ == "__main__":
    unittest.main()
