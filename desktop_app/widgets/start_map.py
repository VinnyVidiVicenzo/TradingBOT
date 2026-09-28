"""One square per starting month: how it went if you had started then.

Rows are years, columns are months. Each square has a symbol as well as a
colour, so the map still reads for anyone who can't tell red from green.

The legend sits in its own block below the grid rather than inside it: a legend
row spanning the grid would stretch every month column to fit it.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Sequence

from crypto_research.timeutil import to_datetime

from .. import metrics_presenter, theme
from ..sweep import WindowOutcome
from ..sweep_summary import Bucket, bucket_for
from ..tooltip import Tooltip, attach_tooltip

# bucket -> (background, text colour, symbol)
_STYLE: dict[Bucket, tuple[str, str, str]] = {
    Bucket.DOUBLED: ("#0f6e56", "#ffffff", "++"),
    Bucket.GAINED: ("#9fe1cb", "#04342c", "+"),
    Bucket.LOST_SOME: ("#f7c1c1", "#501313", "-"),
    Bucket.LOST_MOST: ("#a32d2d", "#ffffff", "--"),
}
# (bucket, words shown, which is also the glossary term that explains it)
LEGEND: tuple[tuple[Bucket, str], ...] = (
    (Bucket.DOUBLED, "doubled or better"),
    (Bucket.GAINED, "made money"),
    (Bucket.LOST_SOME, "lost some"),
    (Bucket.LOST_MOST, "lost over half"),
)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_EMPTY_BG = "#e4e3dd"


def _square(parent: tk.Widget, bucket: Bucket | None) -> tk.Label:
    if bucket is None:
        return tk.Label(parent, width=3, bg=_EMPTY_BG, font=theme.SMALL_FONT)
    background, foreground, symbol = _STYLE[bucket]
    return tk.Label(
        parent, text=symbol, width=3, bg=background, fg=foreground,
        font=("Segoe UI", 8, "bold"),
    )


class StartMap(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self.grid_frame = ttk.Frame(self)
        self.legend_frame = ttk.Frame(self)
        self.grid_frame.pack(anchor="w")
        self.legend_frame.pack(anchor="w", pady=(8, 0))

    def render(self, outcomes: Sequence[WindowOutcome]) -> None:
        for child in (*self.grid_frame.winfo_children(), *self.legend_frame.winfo_children()):
            child.destroy()
        by_month = {}
        for outcome in outcomes:
            moment = to_datetime(outcome.start_ms)
            by_month[(moment.year, moment.month)] = outcome
        if not by_month:
            return

        first_year = min(year for year, _ in by_month)
        last_year = max(year for year, _ in by_month)
        for column, name in enumerate(_MONTHS, start=1):
            ttk.Label(
                self.grid_frame, text=name, font=theme.SMALL_FONT, foreground=theme.MUTED_COLOR
            ).grid(row=0, column=column)
        for row, year in enumerate(range(first_year, last_year + 1), start=1):
            ttk.Label(
                self.grid_frame, text=str(year), font=theme.SMALL_FONT, foreground=theme.MUTED_COLOR
            ).grid(row=row, column=0, sticky="e", padx=(0, 6))
            for month in range(1, 13):
                outcome = by_month.get((year, month))
                square = _square(self.grid_frame, None if outcome is None else bucket_for(outcome))
                square.grid(row=row, column=month, padx=1, pady=1)
                if outcome is not None:
                    Tooltip(square, lambda text=metrics_presenter.cell_tooltip(outcome): text)

        for index, (bucket, meaning) in enumerate(LEGEND):
            item = ttk.Frame(self.legend_frame)
            item.grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 16), pady=1)
            swatch = _square(item, bucket)
            swatch.pack(side="left")
            words = ttk.Label(item, text=meaning, font=theme.SMALL_FONT)
            words.pack(side="left", padx=(4, 0))
            attach_tooltip(swatch, meaning)
            attach_tooltip(words, meaning)
