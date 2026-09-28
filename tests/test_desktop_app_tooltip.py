"""desktop_app.tooltip -- where a hover explanation appears, and its lifecycle."""

from __future__ import annotations

import unittest
from tkinter import ttk

from desktop_app import glossary
from desktop_app.tooltip import Tooltip, attach_tooltip, place_tooltip

from .tk_helpers import TkTestCase

SCREEN = dict(screen_width=1920, screen_height=1080)


class PlaceTooltipTests(unittest.TestCase):
    def _place(self, **overrides):
        values = dict(anchor_x=500, anchor_y=400, anchor_height=20, width=300, height=40, **SCREEN)
        values.update(overrides)
        return place_tooltip(**values)

    def test_normally_just_below_and_to_the_right_of_the_widget(self) -> None:
        self.assertEqual(self._place(), (512, 428))

    def test_is_pushed_left_rather_than_off_the_right_edge(self) -> None:
        x, _y = self._place(anchor_x=1800)
        self.assertEqual(x, 1920 - 300 - 6)

    def test_is_moved_above_the_widget_rather_than_off_the_bottom(self) -> None:
        _x, y = self._place(anchor_y=1040)
        self.assertEqual(y, 1040 - 40 - 6)

    def test_leaves_room_for_a_taskbar_along_the_bottom(self) -> None:
        # 1080 - 48 = 1032: a tooltip that would end below that flips upward.
        _x, y = self._place(anchor_y=990)  # would end at 990 + 20 + 8 + 40 = 1058
        self.assertLess(y, 990)

    def test_never_leaves_the_left_or_top_edge(self) -> None:
        x, y = self._place(anchor_x=-500, anchor_y=2, width=300, height=900)
        self.assertGreaterEqual(x, 6)
        self.assertGreaterEqual(y, 6)

    def test_a_tooltip_wider_than_the_screen_still_starts_on_it(self) -> None:
        x, _y = self._place(width=5000)
        self.assertGreaterEqual(x, 6)


class TooltipLifecycleTests(TkTestCase):
    def _tooltips(self) -> list:
        import tkinter as tk

        found, stack = [], list(self.root.winfo_children())
        while stack:
            widget = stack.pop()
            if isinstance(widget, tk.Toplevel):
                found.append(widget)
            stack.extend(widget.winfo_children())
        return found

    def setUp(self) -> None:
        super().setUp()
        self.label = ttk.Label(self.root, text="hover me")
        self.label.pack()
        self.root.update()

    def test_showing_creates_exactly_one_viewable_tooltip_with_the_text(self) -> None:
        tip = Tooltip(self.label, lambda: "an explanation")
        tip._show()
        self.root.update()
        (window,) = self._tooltips()
        self.assertTrue(window.winfo_viewable())
        texts = [child.cget("text") for child in window.winfo_children()]
        self.assertEqual(texts, ["an explanation"])

    def test_showing_twice_does_not_stack_tooltips(self) -> None:
        tip = Tooltip(self.label, lambda: "text")
        tip._show()
        tip._show()
        self.assertEqual(len(self._tooltips()), 1)

    def test_leaving_removes_it(self) -> None:
        tip = Tooltip(self.label, lambda: "text")
        tip._show()
        tip._hide()
        self.assertEqual(self._tooltips(), [])

    def test_the_tooltip_stays_on_the_screen(self) -> None:
        tip = Tooltip(self.label, lambda: "a long explanation " * 12)
        tip._show()
        self.root.update()
        (window,) = self._tooltips()
        self.assertGreaterEqual(window.winfo_x(), 0)
        self.assertGreaterEqual(window.winfo_y(), 0)
        self.assertLessEqual(window.winfo_x() + window.winfo_width(), self.root.winfo_screenwidth())

    def test_an_empty_explanation_shows_nothing(self) -> None:
        tip = Tooltip(self.label, lambda: "")
        tip._show()
        self.assertEqual(self._tooltips(), [])

    def test_destroying_the_widget_while_a_tooltip_is_pending_is_harmless(self) -> None:
        tip = Tooltip(self.label, lambda: "text")
        tip._schedule()
        self.label.destroy()
        self.root.update()  # must not raise, and must not later pop a tooltip up
        self.assertEqual(self._tooltips(), [])
        self.assertIsNone(tip._after_id)

    def test_hover_events_schedule_and_cancel(self) -> None:
        tip = Tooltip(self.label, lambda: "text")
        self.label.event_generate("<Enter>")
        self.assertIsNotNone(tip._after_id)
        self.label.event_generate("<Leave>")
        self.assertIsNone(tip._after_id)

    def test_attach_looks_the_term_up_at_hover_time_not_at_attach_time(self) -> None:
        original = glossary.DEFINITIONS.get("home")
        self.addCleanup(lambda: glossary.DEFINITIONS.__setitem__("home", original))
        tip = attach_tooltip(self.label, "home")
        glossary.DEFINITIONS["home"] = "edited after attaching"
        tip._show()
        (window,) = self._tooltips()
        self.assertEqual([c.cget("text") for c in window.winfo_children()], ["edited after attaching"])


if __name__ == "__main__":
    unittest.main()
