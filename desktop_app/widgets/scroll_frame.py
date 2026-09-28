"""A frame whose content scrolls vertically when it is taller than the room it has.

Put widgets in ``.content``. The scrollbar only appears when it is needed, and
the mouse wheel scrolls it while the pointer is anywhere over the frame -- over
a label as much as over the scrollbar.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

_STEP_PX = 28


class ScrollFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        background = ttk.Style().lookup("TFrame", "background") or "#f0f0f0"
        self._canvas = tk.Canvas(
            self, highlightthickness=0, borderwidth=0, background=background,
            yscrollincrement=_STEP_PX,
        )
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._scrollbar_shown = False
        self.content = ttk.Frame(self._canvas)
        self._window = self._canvas.create_window((0, 0), window=self.content, anchor="nw")
        self._canvas.configure(yscrollcommand=self._on_scroll_set)
        self._canvas.pack(side="left", fill="both", expand=True)

        self.content.bind("<Configure>", self._on_content_resized)
        self._canvas.bind("<Configure>", self._on_canvas_resized)
        self.bind_all("<MouseWheel>", self._on_wheel, add="+")

    @property
    def scrollbar_visible(self) -> bool:
        return self._scrollbar_shown

    def scroll_to_top(self) -> None:
        self._canvas.yview_moveto(0)

    def scroll_by(self, units: int) -> None:
        """Scroll by ``units`` steps (positive is down). Does nothing if everything fits."""
        if self._scrollbar_shown:
            self._canvas.yview_scroll(units, "units")

    def _on_content_resized(self, event: tk.Event) -> None:
        self._canvas.configure(scrollregion=(0, 0, event.width, event.height))

    def _on_canvas_resized(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)

    def _on_scroll_set(self, first: str, last: str) -> None:
        self._scrollbar.set(first, last)
        needed = float(first) > 0.0 or float(last) < 1.0
        if needed and not self._scrollbar_shown:
            self._scrollbar.pack(side="right", fill="y", before=self._canvas)
            self._scrollbar_shown = True
        elif not needed and self._scrollbar_shown:
            self._scrollbar.pack_forget()
            self._scrollbar_shown = False

    def _on_wheel(self, event: tk.Event) -> None:
        try:
            if not self.winfo_exists():
                return
            target = self.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        if self._contains(target):
            self.scroll_by(-1 if event.delta > 0 else 1)

    def _contains(self, widget: tk.Misc | None) -> bool:
        while widget is not None:
            if widget is self:
                return True
            widget = widget.master
        return False
