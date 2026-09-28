"""'What we've found so far' -- reads results/, computes nothing."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import report_loader, theme
from ..widgets.scrollable_text import ReportView


class FindingsScreen(ttk.Frame):
    def __init__(self, parent: tk.Widget, app: "object") -> None:
        super().__init__(parent)
        self._app = app
        self._entries: list[report_loader.ReportEntry] = []

        ttk.Label(self, text="What we've found so far", font=theme.HEADING_FONT).pack(
            anchor="w", pady=(0, 4)
        )
        self._description = ttk.Label(
            self, font=theme.BODY_FONT, foreground=theme.MUTED_COLOR, wraplength=900,
            justify="left",
        )
        self._description.pack(anchor="w", pady=(0, 8))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        self._list = tk.Listbox(body, width=38, exportselection=False, activestyle="none")
        self._list.pack(side="left", fill="y")
        self._list.bind("<<ListboxSelect>>", self._on_select)

        self._view = ReportView(body)
        self._view.pack(side="left", fill="both", expand=True, padx=(12, 0))

    def on_show(self) -> None:
        if self._entries:
            return
        self._entries = report_loader.catalog(self._app.repo_root)
        for entry in self._entries:
            self._list.insert("end", entry.title)
        if self._entries:
            self._list.selection_set(0)
            self._render(self._entries[0])

    def _on_select(self, _event: object) -> None:
        selection = self._list.curselection()
        if selection:
            self._render(self._entries[selection[0]])

    def _render(self, entry: report_loader.ReportEntry) -> None:
        self._description.configure(text=entry.description)
        self._view.render(
            report_loader.load(self._app.repo_root, entry),
            monospace=not entry.is_markdown,
        )
