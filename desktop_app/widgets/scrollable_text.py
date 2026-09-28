"""Read-only, scrollable renderer for report_loader.Block lists.

Markdown write-ups get a proportional font, with tables drawn as real grids
whose cells wrap. Plain-text reports are hand-aligned ASCII, so they keep a
monospace font and are shown exactly as written.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Sequence

from ..report_loader import Block

_PROSE = ("Segoe UI", 10)
_MONO = ("Consolas", 10)
_TABLE_FONT = ("Segoe UI", 9)
_TABLE_WIDTH_PX = 640
_TABLE_BORDER = "#c9c8c0"
_TABLE_HEADER_BG = "#e8e7e1"
_STYLED_KINDS = {"h1", "h2", "h3", "bullet", "numbered", "quote", "code"}
_PREFIXES = {"bullet": "• "}


class ReportView(ttk.Frame):
    def __init__(self, parent: tk.Widget) -> None:
        super().__init__(parent)
        self.text = tk.Text(
            self, wrap="word", font=_PROSE, state="disabled", borderwidth=0, padx=8, pady=6,
            spacing1=1, spacing3=3, background="#ffffff",
        )
        scrollbar = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._embedded: list[tk.Widget] = []

    def render(self, blocks: Sequence[Block], *, monospace: bool = False) -> None:
        base = _MONO if monospace else _PROSE
        family, size = base
        self.text.configure(state="normal", font=base)
        self._configure_tags(family, size)
        for widget in self._embedded:
            widget.destroy()
        self._embedded = []
        self.text.delete("1.0", "end")  # safety-audit: allow -- Text.delete(), not HTTP

        for block in blocks:
            if block.kind == "blank":
                self.text.insert("end", "\n", () if monospace else ("blank",))
            elif block.kind == "rule":
                self._insert_rule()
            elif block.kind == "table":
                self._insert_table(block)
            else:
                self._insert_text(block)
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)

    def _configure_tags(self, family: str, size: int) -> None:
        tag = self.text.tag_configure
        tag("h1", font=("Segoe UI", 16, "bold"), spacing1=10, spacing3=8)
        tag("h2", font=("Segoe UI", 13, "bold"), spacing1=12, spacing3=6)
        tag("h3", font=("Segoe UI", 11, "bold"), spacing1=8, spacing3=4)
        tag("bold", font=(family, size, "bold"))
        tag("italic", font=(family, size, "italic"))
        tag("code", font=("Consolas", size - 1), background="#eeede8")
        tag("bullet", lmargin1=16, lmargin2=30)
        tag("numbered", lmargin1=16, lmargin2=30)
        tag("quote", lmargin1=14, lmargin2=14, rmargin=10, background="#f1f0ea", spacing1=4, spacing3=4)
        tag("code", font=("Consolas", size - 1), background="#eeede8", lmargin1=14, lmargin2=14)
        tag("blank", font=(family, 5))

    def _insert_text(self, block: Block) -> None:
        prefix = _PREFIXES.get(block.kind, "")
        line_start = self.text.index("end-1c")
        tags = (block.kind,) if block.kind in _STYLED_KINDS else ()
        self.text.insert("end", prefix + block.text + "\n", tags)
        for start, end, style in block.spans:
            self.text.tag_add(
                style,
                f"{line_start}+{len(prefix) + start}c",
                f"{line_start}+{len(prefix) + end}c",
            )

    def _insert_table(self, block: Block) -> None:
        rows = block.cells
        columns = max(len(row) for row in rows)
        weights = [
            max(6, min(60, max(len(row[c]) if c < len(row) else 0 for row in rows)))
            for c in range(columns)
        ]
        total = sum(weights)

        grid = tk.Frame(self.text, background=_TABLE_BORDER)
        for r, row in enumerate(rows):
            for c in range(columns):
                value = row[c] if c < len(row) else ""
                header = r == 0
                wrap = max(70, int(_TABLE_WIDTH_PX * weights[c] / total)) - 14
                cell = tk.Label(
                    grid, text=value, anchor="nw", justify="left", wraplength=wrap,
                    font=(_TABLE_FONT[0], _TABLE_FONT[1], "bold") if header else _TABLE_FONT,
                    background=_TABLE_HEADER_BG if header else "#ffffff",
                    padx=6, pady=3,
                )
                cell.grid(
                    row=r, column=c, sticky="nsew",
                    padx=(1 if c == 0 else 0, 1), pady=(1 if r == 0 else 0, 1),
                )
                cell.bind("<MouseWheel>", self._forward_wheel)
        grid.bind("<MouseWheel>", self._forward_wheel)
        self._embedded.append(grid)
        self.text.window_create("end", window=grid, padx=4, pady=6)
        self.text.insert("end", "\n")

    def _insert_rule(self) -> None:
        line = tk.Frame(self.text, height=1, width=_TABLE_WIDTH_PX, background=_TABLE_BORDER)
        self._embedded.append(line)
        self.text.window_create("end", window=line, padx=4, pady=8)
        self.text.insert("end", "\n")

    def _forward_wheel(self, event: tk.Event) -> None:
        # A label swallows the wheel; hand it to the page so scrolling works over tables.
        self.text.yview_scroll(-1 if event.delta > 0 else 1, "units")
