"""The plan beside a stock fund and a savings account, one column each."""

from __future__ import annotations

from tkinter import ttk

from .. import theme
from ..metrics_presenter import Comparison
from ..tooltip import Tooltip, attach_tooltip

_BOLD = ("Segoe UI", 10, "bold")


class ComparisonTable(ttk.Frame):
    def render(self, comparison: Comparison) -> None:
        for child in self.winfo_children():
            child.destroy()
        columns = len(comparison.columns)

        for index, column in enumerate(comparison.columns, start=1):
            heading = ttk.Label(self, text=column.title, font=_BOLD)
            heading.grid(row=0, column=index, sticky="w", padx=(0, 18), pady=(0, 4))
            attach_tooltip(heading, column.term)
        ttk.Separator(self, orient="horizontal").grid(
            row=1, column=0, columnspan=columns + 1, sticky="ew", pady=(0, 2)
        )

        for row_index, row in enumerate(comparison.rows, start=2):
            label = ttk.Label(self, text=row.label, font=theme.BODY_FONT)
            label.grid(row=row_index, column=0, sticky="w", padx=(0, 18), pady=3)
            attach_tooltip(label, row.term)
            for column_index, cell in enumerate(row.cells, start=1):
                value = ttk.Label(self, text=cell.text, font=_BOLD)
                value.grid(row=row_index, column=column_index, sticky="w", padx=(0, 18), pady=3)
                if cell.tooltip:
                    Tooltip(value, lambda text=cell.tooltip: text)
