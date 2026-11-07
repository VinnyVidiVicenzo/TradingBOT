"""A full-sized swing-practice workspace, independent of the hourly agent."""
from __future__ import annotations

from tkinter import ttk

from paper_trading.swing_strategy import reference_equity
from ..widgets.swing_practice import SwingPracticePanel


class SwingScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.repo_root = app.repo_root
        self.panel = SwingPracticePanel(self, app.repo_root)
        self.panel.pack(fill="both", expand=True)

    def on_show(self):
        state = self.panel.agent.snapshot()
        if state:
            self.panel.render(state)
            return
        path = self.repo_root / "data" / "practice_agent.sqlite"
        amount = reference_equity(path)
        if amount is not None:
            self.panel.seed_amount(amount)

    def close(self):
        self.panel.close()
