"""Controls for the separate frozen swing-practice session."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from crypto_research.market_universe import SYMBOLS
from crypto_research.timeutil import to_iso
from paper_trading import historical
from paper_trading.swing_strategy import SwingPracticeAgent
from market_capture import practice_worker
from ..threading_support import run_in_background
from ..tooltip import Tooltip
from .scroll_frame import ScrollFrame


class SwingPracticePanel(ttk.Frame):
    def __init__(self, parent, repo_root):
        super().__init__(parent)
        self.repo_root = repo_root
        self.agent = SwingPracticeAgent(repo_root / "data" / "swing_practice.sqlite")
        self.history_dir = repo_root / "data" / "learning_research"
        self._timer, self._cancel = None, None
        self._busy, self._closed, self._seeded = False, False, False
        self._seed_value = None
        ttk.Label(self, text="Swing practice", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        controls = ttk.Frame(self)
        controls.pack(anchor="w", pady=5)
        ttk.Label(controls, text="Starting pretend cash ($):").pack(side="left")
        self.amount = tk.StringVar()
        self.entry = ttk.Entry(controls, textvariable=self.amount, width=13)
        self.entry.pack(side="left", padx=5)
        self.start_button = ttk.Button(controls, text="Start swing practice", command=self.start)
        self.start_button.pack(side="left", padx=5)
        ttk.Button(controls, text="Pause swing", command=self.pause).pack(side="left")
        Tooltip(self.entry, lambda: "For a new swing session only. The initial suggestion mirrors your existing pretend wallet and can be edited. "
                "It is not an investment recommendation or a deposit. Existing sessions resume their saved amounts, holdings and model.")
        rules = ttk.Label(self, text="Current model stays fixed. New candidates learn separately from future outcomes, including waits.\n"
                          "Exit: 24 hours, trend reversal, -5% or +10%; portfolio loss trigger: 20% of starting cash (not guaranteed).", wraplength=930)
        rules.pack(anchor="w", pady=3)
        Tooltip(rules, lambda: "Practice only, using public prices. Each coin is capped at 25% of starting pretend capital; available cash is shared. "
                "Fractional fills are estimates. New candidates cannot change this wallet, its saved model or its history.")
        self.status = tk.StringVar(value="Not started. Run historical training first, then start this separate practice session.")
        ttk.Label(self, textvariable=self.status, wraplength=930, justify="left").pack(anchor="w", pady=3)
        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary, wraplength=930, justify="left").pack(anchor="w", pady=3)
        self.waiting_tree = ttk.Treeview(self, columns=("coin", "current", "candidate", "trend", "reason"), show="headings", height=5)
        for key, title, width in (("coin", "Coin", 70), ("current", "Current score", 95), ("candidate", "Learning score", 100), ("trend", "Trend now", 95), ("reason", "What it is waiting for", 555)):
            self.waiting_tree.heading(key, text=title)
            self.waiting_tree.column(key, width=width, stretch=key == "reason")
        for symbol in SYMBOLS:
            self.waiting_tree.insert("", "end", iid=symbol, values=(symbol, "—", "—", "—", "Waiting for a fresh check"))
        self.waiting_tree.pack(fill="x", pady=5)
        Tooltip(self.waiting_tree, lambda: "Current score must reach 0.600 and the trend must be over +0.200%. "
                "Learning score belongs to a separate, changing candidate. Scores are uncalibrated, not proven win probabilities.")
        self.waiting = tk.StringVar()
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, pady=3)
        learning_pane = ScrollFrame(self.tabs)
        self.tabs.add(learning_pane, text="Learning progress")
        self.learning_text = tk.StringVar(value="Learning begins with new observations. The first 24-hour outcome takes at least a day.")
        ttk.Label(learning_pane.content, textvariable=self.learning_text, wraplength=900, justify="left", padding=8).pack(anchor="w")
        comparisons = ttk.Frame(self.tabs)
        self.tabs.add(comparisons, text="Candidate comparison")
        self.comparison_status = tk.StringVar(value="A matched comparison starts after a candidate is frozen.")
        ttk.Label(comparisons, textvariable=self.comparison_status, wraplength=900, padding=5).pack(anchor="w")
        self.comparison = ttk.Treeview(comparisons, columns=("name", "return", "drawdown", "trades", "fees"), show="headings", height=5)
        for key, title in (("name", "Separate paper portfolio"), ("return", "Return"), ("drawdown", "Max drawdown"), ("trades", "Completed exits"), ("fees", "Estimated fees")):
            self.comparison.heading(key, text=title)
            self.comparison.column(key, width=230 if key == "name" else 145, stretch=True)
        for key, title in (("reference", "Frozen reference"), ("candidate", "Frozen candidate"), ("stress", "Candidate: increased costs"), ("buy_hold", "Buy and hold"), ("cash", "Cash")):
            self.comparison.insert("", "end", iid=key, values=(title, "Not started", "—", "—", "—"))
        self.comparison.pack(fill="both", expand=True)
        recent = ScrollFrame(self.tabs)
        self.tabs.add(recent, text="Recent decisions")
        self.events = tk.StringVar()
        ttk.Label(recent.content, textvariable=self.events, wraplength=900, justify="left", padding=8).pack(anchor="w")
        footer = ttk.Frame(self)
        footer.pack(side="bottom", fill="x", before=self.tabs)
        self.worker_text = tk.StringVar(value=practice_worker.describe(repo_root, "swing"))
        ttk.Label(footer, textvariable=self.worker_text, wraplength=930).pack(anchor="w", pady=(3, 0))
        ttk.Label(footer, text="Practice only • Background checks continue with this window closed while the computer is awake • Pause stops decisions", wraplength=930).pack(anchor="w", pady=(3, 0))
        if self.agent.path.exists():
            self.render(self.agent.snapshot())
        self._timer = self.after(2000, self.check)
        self.bind("<Destroy>", self._destroy, add="+")

    def seed_amount(self, value):
        if not self.agent.path.exists() and (not self.amount.get() or self.amount.get() == self._seed_value):
            self.amount.set(value)
            self._seed_value = str(value)

    def render(self, state):
        if not state:
            return
        self.amount.set(state["initial"])
        self.entry.state(["disabled"])
        start, equity = float(state["initial"]), float(state["equity"])
        profile = state["config"]["profile"]
        fee = float(max(v["taker"] for v in profile["fees"].values())) * 100
        provenance = "published; account unverified" if profile.get("verification") == "published_schedule" else "saved fee snapshot"
        self.status.set(("RUNNING — " if state["active"] else "PAUSED — ") + state["message"])
        worker = practice_worker.status(self.repo_root)
        connected = worker.get("active") and not worker["stale"]
        self.start_button.state(["disabled"] if self._busy or (state["active"] and connected) else ["!disabled"])
        self.start_button.configure(text="Reconnect swing" if state["active"] and not connected else "Start swing practice")
        self.summary.set(f"Cash ${float(state['cash']):.2f} | total ${equity:.2f} | return {(equity/start-1)*100:+.2f}% | " +
                        f"max drawdown {float(state['drawdown'])*100:.2f}% | {state['exits']} exits | estimated fees ${float(state['fees']):.2f}" +
                        f"\nTaker fee {fee:.4f}% per side ({provenance}). Holdings: " + (", ".join(state["positions"]) or "none"))
        lines = [f"{symbol}: score {row['score']:.3f}/0.600 | trend {float(row['trend_gap'])*100:+.3f}% | {row['reason']}"
                 for symbol, row in state["waiting"].items()]
        self.waiting.set("\n".join(lines))
        learning = state.get("learning", {})
        predictions = learning.get("latest_predictions", {})
        for symbol in SYMBOLS:
            row = state["waiting"].get(symbol)
            if row:
                self.waiting_tree.item(symbol, values=(symbol, f"{row['score']:.3f}", f"{predictions[symbol]:.3f}" if symbol in predictions else "Collecting",
                                                       f"{float(row['trend_gap'])*100:+.3f}%", row["reason"]))
        self.render_learning(learning)
        self.events.set("\n".join(f"{to_iso(e['at'])}  {e['action']} {e['symbol']} — {e['reason']}" for e in state.get("activity", [])))

    def render_learning(self, learning):
        for key in self.comparison.get_children():
            title = self.comparison.item(key, "values")[0]
            self.comparison.item(key, values=(title, "Not started", "—", "—", "—"))
        self.comparison_status.set("A matched comparison starts after a candidate is frozen.")
        if learning.get("error"):
            self.learning_text.set("Learning is unavailable; wallet decisions remain separate.\n" + learning["error"])
            return
        if learning.get("waiting") or not learning:
            self.learning_text.set(learning.get("message", "Waiting for new learning observations."))
            return
        counts = learning["counts"]
        lines = [learning["message"],
                 f"Examples saved: {sum(counts.values())} | resolved: {counts.get('resolved', 0)} | pending: {counts.get('pending', 0)} | expired: {counts.get('expired', 0)}",
                 f"Candidate trained on {learning['labels']} outcomes across {learning['training_days']} observed days.",
                 "First candidate freeze needs at least 100 resolved outcomes and 7 observed outcome days."]
        if learning.get("next_due"):
            lines.append("Next pending outcome due (UTC): " + to_iso(learning["next_due"]) + ". Missing/late outcomes expire; nothing is backfilled.")
        trial = learning.get("trial")
        if trial:
            lines.append(f"{trial['id']} frozen for a fixed 35-day future comparison. Nonoverlapping outcomes scored: {trial['scored']}/100; complete weeks: {trial['complete_weeks']}/4.")
            if trial.get("ends_at"):
                lines.append("Comparison ends (UTC): " + to_iso(trial["ends_at"]))
            self.comparison_status.set(f"Current comparison: {trial['id']}. Separate matched paper wallets; active wallet is unchanged.")
        if learning.get("last_validation"):
            result = learning["last_validation"]
            lines.append(f"Last candidate {result['id']}: " + ("qualified for research review" if result["qualified"] else "did not qualify"))
            lines.extend(result["reasons"])
            if not trial:
                self.comparison_status.set(f"Completed comparison: {result['id']}. These are archived results, not a new trial.")
        metrics = trial["metrics"] if trial else (learning.get("last_validation") or {}).get("metrics", {})
        for key, item in metrics.items():
            self.comparison.item(key, values=(self.comparison.item(key, "values")[0], f"{item['return_pct']:+.2f}%", f"{item['drawdown_pct']:.2f}%", item["exits"], f"${float(item['fees']):.2f}"))
        if metrics:
            self.comparison.item("cash", values=("Cash", "0.00%", "0.00%", 0, "$0.00"))
        lines.extend(["Current wallet/model never changes automatically. The comparison portfolios use separate pretend cash, matched fees and risk rules.",
                      "Learning milestones do not block the current strategy's entries. Its score, trend, cash and risk rules still decide those entries.",
                      "Hourly training targets overlap. Validation selects nonoverlapping targets per coin and groups errors by week; correlated markets still limit certainty.",
                      "Observed downside uses sampled quotes and can miss moves between checks. No trade or profit is guaranteed."])
        lines.append("Learning follows fresh background swing checks while the computer is awake. Pause stops observation; closing this window does not. Missed history is not invented.")
        self.learning_text.set("\n\n".join(lines))

    def start(self):
        if self._busy or self._closed:
            return
        try:
            if not self.agent.path.exists():
                report = historical.latest(self.history_dir)
                if not report:
                    raise ValueError("Train and evaluate saved hourly history first")
                self.agent.initialize(self.amount.get(), report)
            self.render(self.agent.set_active(True))
            self._busy = True
            self._cancel = run_in_background(self, lambda: practice_worker.start(self.repo_root), self.done)
        except Exception as exc:
            messagebox.showerror("Swing practice not started", str(exc), parent=self)

    def check(self):
        if self._timer is not None:
            self.after_cancel(self._timer)
        self._timer = None
        if self._closed:
            return
        try:
            self.render(self.agent.snapshot())
            self.worker_text.set(practice_worker.describe(self.repo_root, "swing"))
        except Exception as exc:
            self.worker_text.set(f"Could not refresh saved swing status: {str(exc)[:160]}")
        self._timer = self.after(2000, self.check)

    def done(self, result):
        self._busy = False
        self._cancel = None
        if self._closed:
            return
        self.check()
        if not result.ok:
            self.worker_text.set(f"Worker connection failed: {result.error}. Press Reconnect swing to retry.")

    def pause(self):
        if self.agent.path.exists():
            state = self.agent.set_active(False)
            if not self._closed:
                self.render(state)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        if self._cancel:
            self._cancel()
            self._cancel = None

    def _destroy(self, event):
        if event.widget is self:
            self.close()
