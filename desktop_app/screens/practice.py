"""Start/stop the forward-only $200 practice agent."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from paper_trading.practice import PracticeAgent, initial_state, SYMBOLS
from paper_trading.learning import LABELS, initial_learning
from crypto_research.timeutil import now_ms, to_iso
from market_capture import practice_worker

from ..threading_support import run_in_background
from ..tooltip import Tooltip
from ..widgets.scroll_frame import ScrollFrame
from ..widgets.research_panel import ResearchPanel
from ..widgets.metals_panel import MetalsPanel
from .. import practice_status


class PracticeScreen(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.agent = None
        self.repo_root = app.repo_root
        self._cancel = None
        self._path = app.repo_root / "data" / "practice_agent.sqlite"
        self._busy = False
        self._timer = None
        self._closed = False
        self._status_timer = None
        ttk.Label(self, text="Practice agent — saved pretend wallet", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(self, text="BTC, ETH, SOL, ADA and XRP | Public Binance.US prices | Practice only — no account connection.",
                  wraplength=880).pack(anchor="w", pady=4)
        rules = ttk.Label(self, text="Entries use hourly averages. Exits are checked each price refresh. Up to $50 per coin.\n"
                         "Exit on a downward trend, -5% or +10% from entry; stop at a $40 loss trigger (not guaranteed).",
                         wraplength=880)
        rules.pack(anchor="w", pady=4)
        Tooltip(rules, lambda: "Starts with a 0.2% entry threshold. Background trials also test 0.5% and 1.0%, with identical costs and limits. "
                "Available cash is shared across simultaneous qualifying entries, up to $50 per coin, with a $10 minimum allocation. "
                "A rule changes only after a separate forward validation. Background checks continue about once a minute while the computer is awake.")
        self.summary = tk.StringVar()
        summary = ttk.Label(self, textvariable=self.summary, font=("Segoe UI", 12, "bold"))
        summary.pack(anchor="w", pady=8)
        Tooltip(summary, lambda: "Starts with $200 total pretend cash, with no extra deposits or borrowing. "
                "Sales return cash to the same wallet. Total value estimates sale proceeds after assumed costs; "
                "it is only updated after a successful check.")
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, wraplength=880).pack(anchor="w")
        self.refresh_text = tk.StringVar()
        ttk.Label(self, textvariable=self.refresh_text, wraplength=930).pack(anchor="w", pady=(3, 2))
        self.waiting = ttk.Treeview(self, columns=("coin", "blocker", "gap", "target", "next"),
                                   show="headings", height=len(SYMBOLS), selectmode="none")
        for column, label, width in (("coin", "Coin", 65), ("blocker", "What it is waiting for", 290),
                                     ("gap", "Trend now", 100), ("target", "Buy requires", 110),
                                     ("next", "Next entry review (estimate)", 285)):
            self.waiting.heading(column, text=label)
            self.waiting.column(column, width=width, stretch=column in ("blocker", "next"))
        for symbol in SYMBOLS:
            self.waiting.insert("", "end", iid=symbol, values=(symbol, "Waiting for prices", "Unknown", "", ""))
        self.waiting.pack(fill="x", pady=(2, 4))
        Tooltip(self.waiting, lambda: "Trend now compares the 20-hour average with the 50-hour average. "
                "It must be strictly above Buy requires. This is not a price target or a promise of a trade. "
                "The next review waits for a new completed hourly candle and the following price check. "
                "Market-filter and prediction results are advisory/comparison only.")
        self.learning = tk.StringVar()
        learner_label = ttk.Label(self, textvariable=self.learning, wraplength=880)
        learner_label.pack(anchor="w", pady=4)
        Tooltip(learner_label, self._learning_help)
        buttons = ttk.Frame(self)
        buttons.pack(anchor="w", pady=10)
        self.start_button = ttk.Button(buttons, text="Start practice", command=self.start)
        self.start_button.pack(side="left", padx=(0, 8))
        stop = ttk.Button(buttons, text="Pause agent", command=self.pause)
        stop.pack(side="left")
        Tooltip(stop, lambda: "Stop new decisions. Existing pretend holdings stay in your wallet. "
                "Closing the window leaves background checks running. Resume with Start practice; the wallet is never reset automatically.")
        self.worker_text = tk.StringVar(value=practice_worker.describe(self.repo_root, "hourly"))
        ttk.Label(self, textvariable=self.worker_text, wraplength=930).pack(anchor="w", pady=(0, 3))
        ttk.Label(self, text="Assumed costs: 0.4% fee per side, plus 0.1% price allowance and the current buy/sell price gap.\n"
                  "Fractional practice fills are estimates; venue lot-size rules, partial fills and queue priority are not modeled.",
                  wraplength=880).pack(anchor="w", pady=(0, 6))
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True)
        area = ttk.Frame(self.tabs)
        self.tabs.add(area, text="Decisions")
        self.market_text = tk.StringVar(value="Market activity is collected on the next successful practice check.")
        self.comparison_text = tk.StringVar(value="Waiting for matched market observations.")
        self.details_text = tk.StringVar(value="Select a decision to inspect its evidence, costs and conflicts.")
        self.prediction_text = tk.StringVar(value="Opportunity learning starts on the next successful check.")
        for title, variable in (("Market context", self.market_text), ("Signal comparison", self.comparison_text),
                                ("Why this move", self.details_text), ("Opportunity learning", self.prediction_text)):
            pane = ScrollFrame(self.tabs)
            pane.content.configure(padding=8)
            ttk.Label(pane.content, textvariable=variable, wraplength=850, justify="left").pack(anchor="w")
            self.tabs.add(pane, text=title)
        self.research_panel = ResearchPanel(self.tabs, app.repo_root)
        self.tabs.add(self.research_panel, text="Swing & execution")
        self.metals_panel = MetalsPanel(self.tabs, app.repo_root / "data" / "metals_inventory.json")
        self.tabs.add(self.metals_panel, text="Physical metals")
        self.activity = ttk.Treeview(area, columns=("time", "action", "coin", "amount", "reason"), show="headings", height=8)
        for column, title, width in (("time", "Time (UTC)", 155), ("action", "Action", 60),
                                     ("coin", "Coin", 80), ("amount", "Cash", 70), ("reason", "Why", 480)):
            self.activity.heading(column, text=title)
            self.activity.column(column, width=width, stretch=column == "reason")
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.activity.yview)
        self.activity.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.activity.pack(fill="both", expand=True)
        self.activity.bind("<<TreeviewSelect>>", self._selected)
        self.activity.bind("<Double-1>", lambda _event: self.tabs.select(3))
        Tooltip(self.activity, lambda: "Select a row, then open Why this move to inspect its evidence. Double-click a row to open it directly.")
        self.render(initial_state())
        self.bind("<Destroy>", self._destroy_status_timer, add="+")
        self._status_timer = self.after(1000, self._tick_status)

    def render(self, state):
        self._state = state
        self._entries = {}
        holdings = ", ".join(symbol.removesuffix("USD") for symbol in state["positions"]) or "none"
        self.summary.set(f"Cash: ${float(state['cash']):.2f}    Total value: ${float(state['equity']):.2f}    "
                         f"Fees: ${float(state['fees']):.2f}    Holdings: {holdings}")
        self.status.set(("PRACTICE RUNNING — " if state["active"] else "PAUSED — ") + state["message"])
        learner = state.get("learning", initial_learning())
        self._learner = learner
        self.learning.set(f"Learning: {LABELS[learner['active_rule']]} | Completed trades: {learner['actual_exits']} "
                          f"({learner['actual_wins']} profitable) | Realized result: ${float(learner['actual_pnl']):.2f}\n"
                          f"{learner['message']}")
        worker = practice_worker.status(self.repo_root)
        connected = worker.get("active") and not worker["stale"]
        self.start_button.state(["disabled"] if self._busy or (state["active"] and connected) else ["!disabled"])
        self.start_button.configure(text="Reconnect practice" if state["active"] and not connected else "Start practice")
        for row in self.activity.get_children():
            self.activity.delete(row)  # safety-audit: allow -- clears a local Tk tree row
        for entry in state["activity"]:
            amount = entry.get("spend", entry.get("proceeds"))
            cash = ("-" if entry["action"] == "BUY" else "+") + f"${float(amount):.2f}" if amount is not None else ""
            row = self.activity.insert("", "end", values=(to_iso(entry["at"]), entry["action"],
                                       entry["symbol"], cash, practice_status.reason(entry)))
            self._entries[row] = entry
        self._render_market(state)
        self._render_prediction(state)
        self._refresh_waiting()
        self.research_panel.render(state)

    def _refresh_waiting(self):
        at = now_ms()
        self.refresh_text.set(practice_status.refresh_status(self._state, at, busy=self._busy))
        for row in practice_status.entry_rows(self._state, at):
            self.waiting.item(row["symbol"], values=(row["symbol"], row["blocker"], row["gap"], row["target"], row["next"]))

    def _tick_status(self):
        self._status_timer = None
        if self._closed:
            return
        try:
            if self._path.exists():
                self._ensure_agent()
                self.render(self.agent.snapshot())
            self.worker_text.set(practice_worker.describe(self.repo_root, "hourly"))
        except Exception as exc:
            self.worker_text.set(f"Could not refresh saved practice status: {str(exc)[:160]}")
        self._refresh_waiting()
        self._status_timer = self.after(2000, self._tick_status)

    def _destroy_status_timer(self, event=None):
        if event is not None and event.widget is not self:
            return
        if self._status_timer is not None:
            self.after_cancel(self._status_timer)
            self._status_timer = None

    def _render_prediction(self, state):
        model = state.get("prediction")
        if not model:
            self.prediction_text.set("Waiting for future observations. Existing wallet and learning history are preserved.")
            return
        if model.get("error"):
            self.prediction_text.set(model["error"])
            return
        counts, score = model["counts"], model["score"]
        lines = ["Advisory model only — never changes purchases, sales or spending limits.",
                 "Task: estimate whether a hypothetical purchase now would gain after costs about one hour later.",
                 f"Learned outcomes: {model['labels']} | Pending: {counts.get('pending', 0)} | Expired: {counts.get('expired', 0)}",
                 f"Active advisory model: {model['champion']['id']} | Updates accepted: {model['promotions']} | Rollbacks: {model['rollbacks']}",
                 model["message"]]
        if model["champion"]["id"] == "baseline-0":
            lines.append("The active 0.50 estimate is a neutral reference, not a validated 50% chance of profit.")
        if score["n"]:
            lines.append(f"All issued forecasts, across model versions (Brier; lower is better): model {score['model'] / score['n']:.3f}, "
                         f"past-frequency baseline {score['baseline'] / score['n']:.3f}")
        trial = model["trial"]
        if trial:
            lines.append(f"Frozen challenger {trial['id']}: {trial['n']}/100 future validation outcomes; at least seven elapsed days required.")
        else:
            lines.append(f"Collecting training outcomes; next checkpoint at {model['next_freeze']} total outcomes.")
        if model["regimes"]:
            lines.append("Observed conditions (outcomes / model error / baseline error):")
            for name, values in model["regimes"].items():
                lines.append(f"  {name}: {values['n']} / {values['model'] / values['n']:.3f} / {values['baseline'] / values['n']:.3f}")
        if state.get("prediction_warning"):
            lines.append(state["prediction_warning"])
        lines.extend(["", "Recent opportunities — estimates are uncalibrated, not reliable confidence scores:"])
        for example in model["recent"]:
            outcome = (f"hypothetical net move {float(example['net_return']) * 100:+.2f}%"
                       if example["status"] == "resolved" else example["status"])
            lines.append(f"{to_iso(example['predicted_at'])} {example['symbol']} {example['action']} | "
                         f"model estimate {example['probability']:.2f} | candidate {example['candidate_probability']:.2f} | {outcome}")
        lines.extend(["", "Predictions are saved before outcomes; missed one-hour targets expire after five minutes. "
                      "Hypothetical moves do not represent executable trades or portfolio returns. "
                      "Correlated hourly outcomes and changing markets limit these comparisons."])
        self.prediction_text.set("\n".join(lines))

    def _render_market(self, state):
        markets = {}
        for entry in state["activity"]:
            if entry.get("evidence") and entry["symbol"] not in markets:
                markets[entry["symbol"]] = entry["evidence"]["market"]
        lines = ["Sampled about once a minute while running; not a continuous live feed.\n"]
        for symbol, sample in markets.items():
            share = f"{float(sample['buy_share']) * 100:.1f}%" if sample.get("buy_share") is not None else "unknown"
            lines.append(f"{symbol}: {sample.get('trend', 'unknown')} | buy-initiated share: {share} | "
                         f"sample: {sample.get('status', 'unavailable')}")
            lines.append(f"Trades: {sample.get('trade_count', 'unknown')} | largest sampled trade: "
                         f"${float(sample.get('largest_trade', 0)):.2f} | nearby sell offers: "
                         f"${float(sample.get('buy_depth', 0)):.2f}")
            if sample.get("sampled_at"):
                lines.append(f"Observed (UTC): {to_iso(sample['sampled_at'])}")
            if sample.get("reason"):
                lines.append(sample["reason"])
            lines.append("")
        lines.append("Buy-initiated means the buyer crossed the spread. Every trade also has a seller. "
                     "These are exchange samples, not whale wallet identities or proof of intent.")
        self.market_text.set("\n".join(lines))
        trial = state.get("market_trial")
        if not trial:
            self.comparison_text.set("Waiting for complete, fresh market observations to start equal comparison wallets.\n"
                                     "The existing wallet and learner continue unchanged; no missing samples are filled in.")
            return
        lines = [f"Matched checks: {trial['checks']} | Started (UTC): {to_iso(trial['started_at'])}",
                 f"Both trials share starting holdings, cash and costs; entry threshold frozen at {float(trial['margin']) * 100:.1f}%.\n"
                 "They are separate from the running wallet and its changing learner.\n"]
        for key, name in (("reference", "Without market filter"), ("signals", "With market filter")):
            result = trial["wallets"][key]
            lines.append(f"{name}: {float(result['return_pct']):+.2f}% | observed maximum fall: "
                         f"{float(result['drawdown']) * 100:.2f}% | completed trades: {result['exits']}")
        lines.extend(["", trial["message"], "Results include assumed costs. Missed/offline checks are not reconstructed. "
                      "The comparison cannot promote signals into the running agent; a few wins prove nothing."])
        self.comparison_text.set("\n".join(lines))

    def _selected(self, _event=None):
        selected = self.activity.selection()
        entry = self._entries.get(selected[0]) if selected else None
        if not entry:
            return
        evidence = entry.get("evidence", {})
        cost = evidence.get("roundtrip_cost_pct")
        self.details_text.set(f"{entry['action']} {entry['symbol']} — {to_iso(entry['at'])}\n\n{practice_status.reason(entry)}\n\n"
                              f"Market evidence (comparison only): {evidence.get('signal_reason', 'No market evidence saved for this older row.')}\n\n"
                              + (f"Estimated full buy/sell cost at unchanged prices: {float(cost):.2f}%\n" if cost is not None else "")
                              + "The market filter can withhold new trial entries. Existing exits and loss limits still apply.")

    def _learning_help(self):
        trials = "\n".join(f"{LABELS[key]}: ${float(p['wallet']['equity']):.2f}, {p['exits']} completed trades"
                           for key, p in self._learner["portfolios"].items())
        return ("Separate pretend trials do not spend your wallet. Each discovery/validation phase needs at least "
                "168 observed hours and 10 completed trades per compared rule. A challenger must beat cash and "
                "the current rule by $1 after costs, with no greater drawdown. Offline hours do not count. "
                "This is not proof of future profits.\n\n" + (trials or "No trial results yet."))

    def start(self):
        if self._busy or self._closed:
            return
        self._ensure_agent()
        self.render(self.agent.set_active(True))
        self._check()

    def pause(self):
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        if self.agent is not None:
            self.render(self.agent.set_active(False))

    def close(self):
        self._closed = True
        self.research_panel.close()
        self._destroy_status_timer()
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        if self._cancel:
            self._cancel()
            self._cancel = None

    def on_show(self):
        self._ensure_agent()
        self.render(self.agent.snapshot())

    def _ensure_agent(self):
        if self.agent is None:
            self.agent = PracticeAgent(self._path)

    def _check(self):
        self._timer = None
        if self._closed or self._busy or not self.agent.snapshot()["active"]:
            return
        self._busy = True
        self.status.set("Connecting the independent practice worker…")
        self.start_button.state(["disabled"])
        self._cancel = run_in_background(self, lambda: practice_worker.start(self.repo_root), self._done)

    def _done(self, result):
        self._busy = False
        self._cancel = None
        if self._closed:
            return
        state = self.agent.snapshot()
        self.render(state)
        if not result.ok:
            self.worker_text.set(f"Worker connection failed: {result.error}. Press Reconnect practice to retry.")
        else:
            self.worker_text.set(practice_worker.describe(self.repo_root, "hourly"))
