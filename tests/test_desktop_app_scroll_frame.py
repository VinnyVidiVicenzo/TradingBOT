"""desktop_app.widgets.scroll_frame -- scrolls only when it has to."""

from __future__ import annotations

from tkinter import ttk

from desktop_app.widgets.scroll_frame import ScrollFrame

from .tk_helpers import TkTestCase, show_offscreen


class ScrollFrameTests(TkTestCase):
    def _frame(self, *, content_height: int, room: int = 200) -> ScrollFrame:
        frame = ScrollFrame(self.root)
        self.frame = frame
        frame.pack(fill="both", expand=True)
        filler = ttk.Frame(frame.content, width=250, height=content_height)
        filler.pack()
        show_offscreen(self.root, 300, room)
        return frame

    def test_content_that_fits_needs_no_scrollbar(self) -> None:
        frame = self._frame(content_height=80)
        self.assertFalse(frame.scrollbar_visible)

    def test_content_taller_than_the_room_gets_a_scrollbar(self) -> None:
        frame = self._frame(content_height=900)
        self.assertTrue(frame.scrollbar_visible)

    def test_the_scrollbar_goes_away_again_when_the_content_shrinks(self) -> None:
        frame = self._frame(content_height=900)
        for child in frame.content.winfo_children():
            child.configure(height=50)
        self.root.update()
        self.root.update()
        self.assertFalse(frame.scrollbar_visible)

    def test_scrolling_moves_the_view_and_scroll_to_top_returns_it(self) -> None:
        frame = self._frame(content_height=900)
        frame.scroll_by(3)
        self.root.update()
        self.assertGreater(frame._canvas.yview()[0], 0.0)
        frame.scroll_to_top()
        self.root.update()
        self.assertEqual(frame._canvas.yview()[0], 0.0)

    def test_scrolling_when_everything_fits_does_nothing(self) -> None:
        frame = self._frame(content_height=80)
        frame.scroll_by(5)
        self.root.update()
        self.assertEqual(frame._canvas.yview()[0], 0.0)

    def test_content_is_stretched_to_the_width_of_the_frame(self) -> None:
        frame = self._frame(content_height=80)
        show_offscreen(self.root, 420, 200)
        self.assertEqual(frame.content.winfo_width(), frame._canvas.winfo_width())

    def test_a_widget_inside_counts_as_being_over_the_frame(self) -> None:
        frame = self._frame(content_height=80)
        inside = ttk.Label(frame.content, text="x")
        inside.pack()
        outside = ttk.Label(self.root, text="y")
        self.assertTrue(frame._contains(inside))
        self.assertTrue(frame._contains(frame))
        self.assertFalse(frame._contains(outside))
        self.assertFalse(frame._contains(None))
        outside.destroy()

    def test_the_wheel_over_a_destroyed_frame_is_ignored_not_an_error(self) -> None:
        frame = self._frame(content_height=900)

        class _Event:
            x_root = y_root = 10
            delta = -120

        frame.destroy()
        frame._on_wheel(_Event())  # must not raise


if __name__ == "__main__":
    import unittest

    unittest.main()
