"""Everything shown for one finished plan: title, table, map and cautions.

Shared by 'Try your own plan' (a fresh result) and 'Your past plans' (a saved
one), so the two can never drift apart. When the stock-fund and savings
reference data is available the table becomes a side-by-side comparison; when it
is not, the plain single-column table is shown instead.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import alternatives, metrics_presenter, sweep_summary, theme
from ..sweep import SweepResult
from .comparison_table import ComparisonTable
from .start_map import StartMap
from .stat_table import StatTable

_WRAP = 860
_GAP = 30  # between the table and the map when they sit side by side


class SweepResultView(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self._title = ttk.Label(self, font=theme.SUBHEADING_FONT, wraplength=_WRAP, justify="left")
        self._subtitle = ttk.Label(self, wraplength=_WRAP, justify="left")
        body = ttk.Frame(self)
        self._left = ttk.Frame(body)
        self._table = StatTable(self._left)
        self._comparison = ComparisonTable(self._left)
        self._map_column = ttk.Frame(body)
        self._map_caption = ttk.Label(
            self._map_column, text="Each square is one starting month. Hover over one for details.",
            font=theme.SMALL_FONT, foreground=theme.MUTED_COLOR,
        )
        self._map = StartMap(self._map_column)
        self._notes = ttk.Label(
            self, font=theme.SMALL_FONT, foreground=theme.MUTED_COLOR,
            wraplength=_WRAP, justify="left",
        )
        self._body = body
        self._stacked = False
        self._arrange(stacked=False)
        # Whenever the space this view sits in changes, put the map beside the table
        # if both fit and below it if they don't, so nothing is ever cut off.
        self.master.bind("<Configure>", self._on_space_changed, add="+")

    def _arrange(self, *, stacked: bool) -> None:
        self._left.pack_forget()
        self._map_column.pack_forget()
        if stacked:
            self._left.pack(side="top", anchor="w")
            self._map_column.pack(side="top", anchor="w", pady=(16, 0))
        else:
            self._left.pack(side="left", anchor="n", padx=(0, _GAP))
            self._map_column.pack(side="left", anchor="n")
        self._stacked = stacked

    def _on_space_changed(self, event: object = None) -> None:
        # A toplevel's bindings are inherited by every widget inside it, so only
        # react to changes in the container itself.
        if event is not None and getattr(event, "widget", self.master) is not self.master:
            return
        available = self.master.winfo_width()
        if available <= 1:  # not laid out yet
            return
        self.update_idletasks()
        needed = self._left.winfo_reqwidth() + _GAP + self._map_column.winfo_reqwidth()
        stacked = needed > available
        if stacked != self._stacked:
            self._arrange(stacked=stacked)

    @property
    def stacked(self) -> bool:
        return self._stacked

    @property
    def showing_comparison(self) -> bool:
        return bool(self._comparison.winfo_manager())

    def clear(self) -> None:
        for widget in (self._title, self._subtitle, self._body, self._notes):
            widget.pack_forget()

    def render(
        self, result: SweepResult, reference: alternatives.ReferenceData | None = None
    ) -> None:
        summary = sweep_summary.summarize(result)
        if summary is None:
            self.clear()
            return

        comparison, compared_months = self._compare(result, summary, reference)
        self._table.pack_forget()
        self._comparison.pack_forget()
        if comparison is not None:
            self._comparison.render(comparison)
            self._comparison.pack(anchor="n")
        else:
            self._table.render(metrics_presenter.rows(summary))
            self._table.pack(anchor="n")

        self._title.configure(text=metrics_presenter.title(result))
        self._subtitle.configure(text=metrics_presenter.subtitle(result, summary))
        self._map.render(result.outcomes)
        self._notes.configure(
            text="\n\n".join(
                metrics_presenter.notes(
                    summary, compared=comparison is not None, compared_months=compared_months
                )
            )
        )

        self.clear()
        self._title.pack(anchor="w")
        self._subtitle.pack(anchor="w", pady=(2, 10))
        self._body.pack(anchor="w")
        self._map_caption.pack(anchor="w", pady=(0, 4))
        self._map.pack(anchor="w")
        self._notes.pack(anchor="w", pady=(14, 0))
        self._on_space_changed()

    @staticmethod
    def _compare(
        result: SweepResult,
        summary: sweep_summary.SweepSummary,
        reference: alternatives.ReferenceData | None,
    ) -> tuple[metrics_presenter.Comparison | None, int | None]:
        if result.comparison_snapshot is not None:
            found = alternatives.restore_snapshot(result.comparison_snapshot)
        elif reference is None:
            return None, None
        else:
            found = alternatives.compare(result, reference)
        if found is None:
            return None, None
        fund = sweep_summary.summarize_outcomes(found.fund, amount=result.amount)
        savings = sweep_summary.summarize_outcomes(found.savings, amount=result.amount)
        starts = {o.start_ms for o in found.fund}
        matched = sweep_summary.summarize_outcomes(
            tuple(o for o in result.outcomes if o.start_ms in starts), amount=result.amount
        )
        return metrics_presenter.comparison(matched, fund, savings), fund.tested
