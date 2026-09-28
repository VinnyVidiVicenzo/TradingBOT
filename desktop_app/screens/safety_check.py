"""One click, right now: prove this app cannot trade or move money."""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from crypto_research.safety import audit_repository

from .. import theme
from ..threading_support import JobResult, run_in_background
from ..tooltip import attach_tooltip

logger = logging.getLogger(__name__)


class SafetyCheckScreen(ttk.Frame):
    def __init__(self, parent: tk.Widget, app: "object") -> None:
        super().__init__(parent)
        self._app = app

        ttk.Label(self, text="Safety check", font=theme.HEADING_FONT).pack(
            anchor="w", pady=(0, 8)
        )
        ttk.Label(
            self,
            text=(
                "This checks the actual code on your computer, right now, for "
                "anything that could place an order or move money. It isn't a "
                "promise -- it's a scan, and you can run it as often as you like."
            ),
            wraplength=600,
            justify="left",
        ).pack(anchor="w", pady=(0, 16))

        self._button = ttk.Button(self, text="Check now", command=self._on_check_clicked)
        self._button.pack(anchor="w")
        attach_tooltip(self._button, "check now")

        self._result_var = tk.StringVar(value="")
        self._result_label = ttk.Label(
            self, textvariable=self._result_var, wraplength=600, justify="left",
            font=theme.SUBHEADING_FONT,
        )
        self._result_label.pack(anchor="w", pady=(14, 0))

    def _on_check_clicked(self) -> None:
        self._button.state(["disabled"])
        self._result_label.configure(foreground="black")
        self._result_var.set("Checking...")

        def work():
            return audit_repository(self._app.repo_root)

        run_in_background(self, work, self._on_check_done)

    def _on_check_done(self, result: JobResult) -> None:
        self._button.state(["!disabled"])
        if not result.ok:
            self._result_label.configure(foreground="#a32d2d")
            self._result_var.set(f"Couldn't finish the check: {result.error}")
            return
        report = result.value
        if report.ok:
            self._result_label.configure(foreground="#0f6e56")
            self._result_var.set(
                "Confirmed: nothing that could trade or move money was found, "
                f"across {report.files_scanned} files scanned just now."
            )
        else:
            self._result_label.configure(foreground="#a32d2d")
            self._result_var.set(
                f"This found {len(report.findings)} issue(s) that shouldn't be "
                "here. Do not use this app until this is fixed -- see "
                "logs/desktop_app.log for detail."
            )
            logger.error("safety audit findings:\n%s", report.summary())
