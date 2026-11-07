"""Safety reassurance and a way into the other screens."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import theme, coin_catalog

_BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("Swing practice", "Run your saved swing wallet and follow its separate learning candidates.", "swing"),
    (
        "Practice agent",
        "Let a $200 pretend wallet decide when to buy, hold and sell using current prices.",
        "practice",
    ),
    (
        "What we've found so far",
        "Read the plain-language findings from the research already done.",
        "findings",
    ),
    (
        "Try your own plan",
        "Pick an amount, some coins and a schedule, and see what it would have done.",
        "try_a_plan",
    ),
    (
        "Your past plans",
        "Look back at plans you've already tried in this app.",
        "past_plans",
    ),
    (
        "Safety check",
        "Check the code for known forbidden patterns and read the check's limits.",
        "safety_check",
    ),
)


class HomeScreen(ttk.Frame):
    def __init__(self, parent: tk.Widget, app: "object") -> None:
        super().__init__(parent)
        self._app = app

        ttk.Label(self, text="Crypto plan tester", font=theme.HEADING_FONT).pack(
            anchor="w", pady=(0, 8)
        )
        ttk.Label(
            self,
            text=(
                "This tool looks at saved and current public prices and does practice math with "
                "made-up money. It can never buy, sell, or move your real money -- "
                "there is no button anywhere in this app that does that."
            ),
            wraplength=600,
            justify="left",
        ).pack(anchor="w", pady=(0, 24))

        options = coin_catalog.discover_available_coins(app.db_path)
        latest = min((o.last_open_date for o in options), default=None)
        ttk.Label(self, text=(f"Saved daily prices available through {latest}. This app does not update them automatically."
                             if latest else "No saved daily prices are available yet."),
                  wraplength=600, justify="left").pack(anchor="w", pady=(0, 12))

        for title, subtitle, key in _BUTTONS:
            row = ttk.Frame(self)
            row.pack(fill="x", pady=6)
            ttk.Button(
                row, text=title, width=26, command=lambda k=key: self._app.show(k)
            ).pack(side="left")
            ttk.Label(row, text=subtitle, wraplength=380, justify="left").pack(
                side="left", padx=14
            )
