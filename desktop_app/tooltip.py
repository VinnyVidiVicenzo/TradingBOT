"""A hover tooltip. Tkinter has none built in.

Looks the term up in glossary.py at hover time (not when attached), so
editing a definition needs no other change anywhere in the app.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from . import glossary

_DELAY_MS = 400
_MARGIN = 6
_TASKBAR = 48  # room left for a taskbar along the bottom of the screen


def place_tooltip(
    *,
    anchor_x: int,
    anchor_y: int,
    anchor_height: int,
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
) -> tuple[int, int]:
    """Where to put a tooltip so that it stays fully on the screen.

    Normally just below and a little to the right of the widget. Pushed left if
    that would run off the right edge, and moved above the widget if it would
    run off the bottom.
    """
    x = anchor_x + 12
    y = anchor_y + anchor_height + 8
    x = max(_MARGIN, min(x, screen_width - width - _MARGIN))
    if y + height > screen_height - _TASKBAR:
        y = max(_MARGIN, anchor_y - height - 6)
    return x, y


class Tooltip:
    def __init__(self, widget: tk.Widget, text_fn: Callable[[], str]) -> None:
        self._widget = widget
        self._text_fn = text_fn
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self._after_id = self._widget.after(_DELAY_MS, self._show)

    def _show(self) -> None:
        self._after_id = None
        if self._tip is not None:
            return
        text = self._text_fn()
        if not text:
            return
        tip = tk.Toplevel(self._widget)
        tip.withdraw()  # measure and place it first, so it never flashes in the wrong spot
        tip.wm_overrideredirect(True)
        tk.Label(
            tip,
            text=text,
            justify="left",
            background="#ffffe0",
            foreground="#000000",
            relief="solid",
            borderwidth=1,
            wraplength=320,
            padx=6,
            pady=4,
        ).pack()
        tip.update_idletasks()
        x, y = place_tooltip(
            anchor_x=self._widget.winfo_rootx(),
            anchor_y=self._widget.winfo_rooty(),
            anchor_height=self._widget.winfo_height(),
            width=tip.winfo_reqwidth(),
            height=tip.winfo_reqheight(),
            screen_width=self._widget.winfo_screenwidth(),
            screen_height=self._widget.winfo_screenheight(),
        )
        tip.wm_geometry(f"+{x}+{y}")
        tip.deiconify()
        self._tip = tip

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    def _cancel(self) -> None:
        if self._after_id is not None:
            self._widget.after_cancel(self._after_id)
            self._after_id = None


def attach_tooltip(widget: tk.Widget, term: str) -> Tooltip:
    """The one call every screen uses to explain a jargon word on hover."""
    return Tooltip(widget, lambda: glossary.define(term))
