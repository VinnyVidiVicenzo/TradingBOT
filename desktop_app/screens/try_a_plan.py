"""Pick an amount, coins, a schedule and a hold; see how it would have gone.

The plan is tried from the 1st of *every* possible starting month (sweep.py)
rather than from one date, because when you start matters more than anything
else a person controls. Each finished sweep is appended to the plan history
(plan_history.py) for the 'Your past plans' screen.
"""

from __future__ import annotations

import logging
from dataclasses import replace
import tkinter as tk
from tkinter import ttk

from crypto_research.timeutil import now_ms, to_iso

from .. import (
    alternatives,
    coin_catalog,
    errors,
    plan_builder,
    plan_history,
    sweep,
    sweep_summary,
    theme,
)
from ..plan_builder import Schedule
from ..sweep import SweepRequest, SweepResult
from ..threading_support import JobResult, run_in_background
from ..tooltip import attach_tooltip
from ..widgets.scroll_frame import ScrollFrame
from ..widgets.sweep_result import SweepResultView

logger = logging.getLogger(__name__)

_DEFAULT_COINS = {"BTC", "ETH"}
_COINS_PER_ROW = 4


class TryAPlanScreen(ttk.Frame):
    def __init__(self, parent: tk.Widget, app: "object") -> None:
        super().__init__(parent)
        self._app = app
        self._coins: list[coin_catalog.CoinOption] = []
        self._coin_vars: dict[str, tk.BooleanVar] = {}
        self._cancel_job = None

        ttk.Label(self, text="Try your own plan", font=theme.HEADING_FONT).pack(
            anchor="w", pady=(0, 6)
        )

        form = ttk.Frame(self)
        form.pack(fill="x", pady=(0, 8))

        self._add_label(form, "How much money?", row=0, sticky="w")
        self._amount_var = tk.StringVar(value="200")
        self._amount_entry = ttk.Entry(form, textvariable=self._amount_var, width=12)
        self._amount_entry.grid(row=0, column=1, sticky="w", padx=12)
        attach_tooltip(self._amount_entry, "how much money?")
        self._amount_entry.bind("<Return>", self._on_return)

        self._add_label(form, "Which coins?", row=1, sticky="nw")
        self._coin_frame = ttk.Frame(form)
        self._coin_frame.grid(row=1, column=1, sticky="w", padx=12)

        self._add_label(form, "How should it go in?", row=2, sticky="w")
        schedule_frame = ttk.Frame(form)
        schedule_frame.grid(row=2, column=1, sticky="w", padx=12)
        self._schedule_var = tk.StringVar(value=Schedule.SPREAD_OVER_WEEKS.value)
        for schedule in Schedule:
            label_text = plan_builder.SCHEDULE_LABELS[schedule]
            radio = ttk.Radiobutton(
                schedule_frame, text=label_text, value=schedule.value,
                variable=self._schedule_var,
            )
            radio.pack(side="left", padx=(0, 16))
            attach_tooltip(radio, label_text.lower())

        self._add_label(form, "How long would you hold it?", row=3, sticky="w")
        hold_frame = ttk.Frame(form)
        hold_frame.grid(row=3, column=1, sticky="w", padx=12)
        self._hold_var = tk.IntVar(value=12)
        for months, label_text in sweep.HOLD_LABELS.items():
            radio = ttk.Radiobutton(
                hold_frame, text=label_text, value=months, variable=self._hold_var
            )
            radio.pack(side="left", padx=(0, 16))
            attach_tooltip(radio, label_text.lower())

        action_row = ttk.Frame(self)
        action_row.pack(fill="x", pady=(0, 6))
        self._run_button = ttk.Button(
            action_row, text="See how it would have gone", command=self._on_run_clicked
        )
        self._run_button.pack(side="left")
        attach_tooltip(self._run_button, "see how it would have gone")
        self._status_var = tk.StringVar(value="")
        self._status_label = ttk.Label(
            action_row, textvariable=self._status_var, wraplength=640, justify="left"
        )
        self._status_label.pack(side="left", padx=14)

        self._scroller = ScrollFrame(self)
        self._scroller.pack(fill="both", expand=True, pady=(8, 0))
        self._results = SweepResultView(self._scroller.content)
        self._results.pack(anchor="nw")

    @staticmethod
    def _add_label(form: ttk.Frame, text: str, *, row: int, sticky: str) -> ttk.Label:
        label = ttk.Label(form, text=text)
        label.grid(row=row, column=0, sticky=sticky, pady=4)
        attach_tooltip(label, text.lower())
        return label

    def on_show(self) -> None:
        if not self._coins:
            self._load_coins()
        self._amount_entry.focus_set()
        self._amount_entry.selection_range(0, "end")

    def _load_coins(self) -> None:
        self._coins = coin_catalog.discover_available_coins(self._app.db_path)
        if not self._coins:
            self._show_status(
                "No price history has been downloaded yet. See the README's Quick Start.",
                error=True,
            )
            return
        for index, coin in enumerate(self._coins):
            var = tk.BooleanVar(value=coin.base_asset in _DEFAULT_COINS)
            self._coin_vars[coin.base_asset] = var
            check = ttk.Checkbutton(self._coin_frame, text=coin.display_name, variable=var)
            check.grid(
                row=index // _COINS_PER_ROW, column=index % _COINS_PER_ROW,
                sticky="w", padx=(0, 16),
            )
            attach_tooltip(check, coin.display_name.lower())

    def _show_status(self, message: str, *, error: bool = False) -> None:
        self._status_label.configure(foreground=theme.ERROR_COLOR if error else theme.MUTED_COLOR)
        self._status_var.set(message)

    def _on_return(self, _event: object = None) -> None:
        self._on_run_clicked()

    def _on_run_clicked(self) -> None:
        if self._run_button.instate(["disabled"]):
            return  # Enter pressed while a run is already working
        self._show_status("")
        selected = [coin for coin in self._coins if self._coin_vars[coin.base_asset].get()]
        if not selected:
            self._show_status("Pick at least one coin.", error=True)
            return
        try:
            amount = plan_builder.parse_amount(self._amount_var.get())
        except ValueError as exc:
            self._show_status(str(exc), error=True)
            return
        shared = coin_catalog.overlapping_range(selected)
        if shared is None:
            self._show_status(
                "These coins don't share any saved price history. Try different coins.",
                error=True,
            )
            return
        request = SweepRequest(
            amount=amount,
            symbols=tuple(coin.symbol for coin in selected),
            schedule=Schedule(self._schedule_var.get()),
            hold_months=self._hold_var.get(),
            first_open_ms=shared[0],
            last_open_ms=shared[1],
            db_path=self._app.db_path,
        )

        self._run_button.state(["disabled"])
        self._results.clear()
        self._show_status("Trying every possible starting month...")
        self._cancel_job = run_in_background(
            self, lambda: sweep.run_sweep(request), self._on_run_done
        )

    def _on_run_done(self, result: JobResult) -> None:
        self._run_button.state(["!disabled"])
        if not result.ok:
            self._show_status(errors.explain(result.error), error=True)
            return
        outcome: SweepResult = result.value
        if len(outcome.outcomes) < sweep.MIN_WINDOWS:
            self._show_status(
                "There isn't enough saved price history for these coins to test holding "
                f"for {sweep.HOLD_LABELS[outcome.hold_months]} from enough different "
                f"starting months (only {len(outcome.outcomes)} could be tried). Try a "
                "shorter hold, or different coins.",
                error=True,
            )
            return
        if sweep_summary.invested_nothing(outcome):
            self._show_status(
                "None of the money was invested: every buy was too small for the exchange "
                "to accept. Try a larger amount, fewer coins, or \"Put it all in at once\".",
                error=True,
            )
            return
        self._show_status("")
        try:
            outcome = replace(outcome, comparison_snapshot=alternatives.snapshot(outcome, self._app.reference))
            plan_history.append(
                self._app.plan_history_path, outcome, created_at_iso=to_iso(now_ms())
            )
        except OSError:
            logger.exception("could not save this plan to the history file")
            self._show_status("(This result couldn't be saved to your past plans.)")
        self._results.render(outcome, self._app.reference)
        self._scroller.scroll_to_top()
