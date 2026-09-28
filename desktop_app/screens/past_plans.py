"""Look back at plans tried through this app, newest first."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from crypto_research.timeutil import parse_time

from .. import metrics_presenter, plan_history, theme
from ..widgets.scroll_frame import ScrollFrame
from ..widgets.sweep_result import SweepResultView


class PastPlansScreen(ttk.Frame):
    def __init__(self, parent: tk.Widget, app: "object") -> None:
        super().__init__(parent)
        self._app = app
        self._plans: list[plan_history.StoredPlan] = []

        ttk.Label(self, text="Your past plans", font=theme.HEADING_FONT).pack(
            anchor="w", pady=(0, 6)
        )

        self._empty_label = ttk.Label(
            self,
            text="You haven't tried a plan yet -- go to \"Try your own plan\".",
            wraplength=640,
            justify="left",
        )

        self._list_frame = ttk.Frame(self)
        self._list = tk.Listbox(self._list_frame, height=5, exportselection=False)
        scrollbar = ttk.Scrollbar(self._list_frame, command=self._list.yview)
        self._list.configure(yscrollcommand=scrollbar.set)
        self._list.pack(side="left", fill="x", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._list.bind("<<ListboxSelect>>", self._on_select)

        self._scroller = ScrollFrame(self)
        self._results = SweepResultView(self._scroller.content)

    def on_show(self) -> None:
        self._plans = sorted(
            plan_history.load_all(self._app.plan_history_path),
            key=lambda plan: parse_time(plan.created_at_iso),
            reverse=True,
        )
        self._list.delete(0, "end")  # safety-audit: allow -- Listbox.delete(), not HTTP
        self._empty_label.pack_forget()
        self._list_frame.pack_forget()
        self._scroller.pack_forget()
        self._results.clear()
        if not self._plans:
            self._empty_label.pack(anchor="w")
            return
        for plan in self._plans:
            when = metrics_presenter.when_label(plan.created_at_iso)
            self._list.insert("end", f"{when}   |   {metrics_presenter.describe(plan.result)}")
        self._list_frame.pack(fill="x", pady=(0, 10))
        self._scroller.pack(fill="both", expand=True)
        self._results.pack(anchor="nw")
        self._list.selection_set(0)
        self._show(self._plans[0])

    def _on_select(self, _event: object) -> None:
        selection = self._list.curselection()
        if selection:
            self._show(self._plans[selection[0]])

    def _show(self, plan: plan_history.StoredPlan) -> None:
        self._results.render(plan.result, self._app.reference)
        self._scroller.scroll_to_top()
