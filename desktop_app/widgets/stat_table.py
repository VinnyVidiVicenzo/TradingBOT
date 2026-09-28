"""Renders plain-language stat rows with hover tooltips attached to each label."""

from __future__ import annotations

from tkinter import ttk
from typing import Sequence

from .. import theme
from ..metrics_presenter import StatRow
from ..tooltip import attach_tooltip


class StatTable(ttk.Frame):
    def render(self, rows: Sequence[StatRow]) -> None:
        for child in self.winfo_children():
            child.destroy()
        for row_index, row in enumerate(rows):
            label_widget = ttk.Label(self, text=row.label, font=theme.BODY_FONT)
            label_widget.grid(row=row_index, column=0, sticky="w", padx=(0, 20), pady=3)
            if row.term:
                attach_tooltip(label_widget, row.term)
            value_widget = ttk.Label(self, text=row.value, font=("Segoe UI", 10, "bold"))
            value_widget.grid(row=row_index, column=1, sticky="w", pady=3)
