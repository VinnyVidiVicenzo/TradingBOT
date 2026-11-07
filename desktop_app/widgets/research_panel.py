"""Swing forecasts, fee snapshots and explicit public stream recording controls."""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from crypto_research.timeutil import now_ms, to_iso
from market_capture.service import Recorder, snapshot
from market_capture import background
from paper_trading import research_costs, historical, swing_backtest
from ..threading_support import run_in_background
from ..tooltip import Tooltip
from .scroll_frame import ScrollFrame


class ResearchPanel(ScrollFrame):
    def __init__(self, parent, repo_root):
        super().__init__(parent)
        self.content.configure(padding=8)
        self.profile_path = repo_root / "data" / "research_costs.json"
        self.collect_dir = repo_root / "data" / "learning_capture"
        self.history_dir = repo_root / "data" / "learning_research"
        self._history_busy = False
        self._cancel_history = None
        self.recorder = Recorder(repo_root / "data" / "market_capture.sqlite")
        self._timer = None
        self._closed = False
        ttk.Label(self.content, text="Research and swing practice — original wallet preserved", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.swing_text = tk.StringVar(value="Swing models begin collecting new 4h/24h outcomes on the next practice check.")
        ttk.Label(self.content, textvariable=self.swing_text, wraplength=840, justify="left").pack(anchor="w", pady=5)
        ttk.Button(self.content, text="Import reported fee snapshot (JSON)", command=self.import_profile).pack(anchor="w", pady=5)
        ttk.Label(self.content, text="Fee snapshots affect new swing studies only. Existing practice fees stay unchanged. "
                  "Refresh rates locally with the separate read-only account tool; never enter account details here.", wraplength=840).pack(anchor="w")
        ttk.Label(self.content, text="Swing practice has its own full-sized screen. Use Swing practice in the top navigation.", wraplength=840).pack(anchor="w", pady=8)
        controls = ttk.Frame(self.content)
        controls.pack(anchor="w", pady=8)
        ttk.Button(controls, text="Start public stream recording (30 min)", command=self.start).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Stop recording", command=self.recorder.stop).pack(side="left")
        self.capture_text = tk.StringVar(value="No stream recorder started in this window.")
        ttk.Label(self.content, textvariable=self.capture_text, wraplength=840).pack(anchor="w", pady=4)
        ttk.Label(self.content, text="Records raw public trades, best quotes and top-20 depth snapshots, with receipt times and gap markers. "
                  "Not a full order book or a guarantee of fills. Stops at 100,000 events / 128 MiB rather than deleting history. "
                  "Capture does not start automatically, and closes with the app. Execution replay is available through python -m market_capture evaluate.",
                  wraplength=840).pack(anchor="w", pady=5)
        ttk.Label(self.content, text="Continuous collection and historical training", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(12, 4))
        continuous = ttk.Frame(self.content)
        continuous.pack(anchor="w")
        start = ttk.Button(continuous, text="Start background collection", command=self.start_background)
        start.pack(side="left", padx=(0, 8))
        ttk.Button(continuous, text="Stop background collection", command=lambda: background.stop(self.collect_dir)).pack(side="left")
        Tooltip(start, lambda: "Starts a separate public-data process. It continues after this interface closes, while the computer is awake. "
                "Captures rotate into new files; collection stops near a 512 MiB quota or 256 files without deleting history. "
                "This does not run practice trades or keep the computer awake. It does not restart automatically after Windows restarts.")
        self.background_text = tk.StringVar(value="Background collection has not been started.")
        ttk.Label(self.content, textvariable=self.background_text, wraplength=840).pack(anchor="w", pady=5)
        history_controls = ttk.Frame(self.content)
        history_controls.pack(anchor="w", pady=5)
        self.download_button = ttk.Button(history_controls, text="Download/update 12 months of hourly history", command=lambda: self.history_job("download"))
        self.download_button.pack(side="left", padx=(0, 8))
        self.evaluate_button = ttk.Button(history_controls, text="Train and evaluate saved history", command=lambda: self.history_job("evaluate"))
        self.evaluate_button.pack(side="left")
        self.portfolio_button = ttk.Button(self.content, text="Test the whole swing portfolio against cash and buy/hold", command=lambda: self.history_job("portfolio"))
        self.portfolio_button.pack(anchor="w", pady=5)
        self.portfolio_text = tk.StringVar()
        ttk.Label(self.content, textvariable=self.portfolio_text, wraplength=840, justify="left").pack(anchor="w", pady=5)
        Tooltip(self.evaluate_button, lambda: "Trains separate candle-only candidates at 1h, 4h and 24h. Each uses three later test periods. "
                "Training labels must be known before each test begins, across all five coins. "
                "Every experiment and exact input is saved. This never replaces practice models. Downloaded candles cannot reconstruct historical spreads or fills.")
        self.history_text = tk.StringVar(value="Download history, then train and evaluate it. Missing hours stay missing; no practice history is backfilled.")
        ttk.Label(self.content, textvariable=self.history_text, wraplength=840, justify="left").pack(anchor="w", pady=5)
        self.load_history_report()
        self.bind("<Destroy>", self._destroy, add="+")
        self._timer = self.after(2000, self._poll)

    def render(self, state):
        model = state.get("swing")
        if not model:
            return
        if model.get("error"):
            self.swing_text.set("Swing research unavailable: " + model["error"])
            return
        profile = model["cost_profile"]
        lines = [f"Research cost source: {profile['source']}",
                 f"Conservative taker fee per side: {research_costs.fee(profile) * 100:.4f}% | slippage allowance: {float(profile['slippage']) * 100:.3f}%"]
        if profile.get("verification") == "published_schedule":
            lines.append("Public schedule checked; account-specific rates/discounts remain unverified. Historical tests apply these costs as a scenario.")
        if profile.get("assumed_symbols"):
            lines.append("Assumed, not reported, fee rates for: " + ", ".join(profile["assumed_symbols"]) + ". Import current rates for all five coins.")
        if profile["observed_at"] is not None:
            lines.append(f"Fee snapshot UTC: {to_iso(profile['observed_at'])}. Imported files are not independently authenticated.")
            if now_ms() - profile["observed_at"] > 7 * 24 * 3600000:
                lines.append("Fee snapshot is over seven days old; check current rates before interpreting results.")
        else:
            lines.append("Assumptions only — account-specific fees have not been verified.")
        for name, track in model["tracks"].items():
            lines.append("")
            if track.get("waiting") or track.get("error"):
                lines.append(f"{name}: {track.get('error', 'waiting for first practice check under these costs')}")
                continue
            lines.append(f"{name} specialist: {track['labels']} learned outcomes, {track['counts'].get('pending', 0)} pending, "
                         f"{track['counts'].get('expired', 0)} expired. Advisory model: {track['champion']['id']}.")
            lines.append(track["message"])
            values = track["evaluation"]["mean_pct"]
            if values["model"] is not None:
                lines.append("Mean hypothetical opportunity result: " + " | ".join(f"{key} {value:+.3f}%" for key, value in values.items()))
        lines.extend(["", "Each specialist uses its own nonoverlapping per-coin horizon and separate frozen future validation. "
                      "Cash is zero return; trend is a simple fixed 0.2% entry filter; all means every observed opportunity; stress doubles fees/slippage. "
                      "These are hypothetical opportunity comparisons, not portfolio returns. Missing future quotes expire; nothing is backfilled. "
                      "These models cannot place trades or change the running strategy. Longer-horizon evidence can take months."])
        if state.get("swing_warning"):
            lines.append(state["swing_warning"])
        self.swing_text.set("\n".join(lines))

    def import_profile(self):
        chosen = filedialog.askopenfilename(parent=self, title="Import reported fee rates", filetypes=(("JSON", "*.json"),))
        if not chosen:
            return
        try:
            from pathlib import Path
            payload = research_costs.load(Path(chosen))
            research_costs.store(self.profile_path, payload)
            self.swing_text.set("Fee profile saved. A separate cost-specific swing study starts on the next practice check; existing studies and wallet are preserved.")
        except Exception as exc:
            messagebox.showerror("Fee profile not imported", str(exc), parent=self)

    def start(self):
        self.recorder.start()
        self.capture_text.set("Starting public stream recorder…")

    def start_background(self):
        try:
            self.background_text.set(background.start(self.collect_dir)["message"])
        except Exception as exc:
            self.background_text.set(f"Background collection could not start: {exc}")

    def load_history_report(self):
        try:
            report = historical.latest(self.history_dir)
            if report:
                self.history_text.set(historical.describe(report))
            portfolio = swing_backtest.latest(self.history_dir)
            if portfolio:
                prefix = "Older portfolio report — rerun the portfolio test for the latest model study.\n" if report and portfolio["historical_report_id"] != report["id"] else ""
                self.portfolio_text.set(prefix + swing_backtest.describe(portfolio))
        except Exception as exc:
            self.history_text.set(f"Cannot read historical research: {exc}")

    def history_job(self, kind):
        if self._history_busy or self._closed:
            return
        self._history_busy = True
        self.download_button.state(["disabled"])
        self.evaluate_button.state(["disabled"])
        self.portfolio_button.state(["disabled"])
        self.history_text.set("Downloading completed public hourly candles…" if kind == "download" else "Training and testing separate historical candidates…")
        directory, profile = self.history_dir, self.profile_path
        work = ((lambda: historical.download(directory)) if kind == "download" else
                (lambda: swing_backtest.evaluate(directory)) if kind == "portfolio" else
                (lambda: historical.evaluate(directory, profile)))
        def done(result):
            self._cancel_history = None
            self._history_busy = False
            if self._closed:
                return
            self.download_button.state(["!disabled"])
            self.evaluate_button.state(["!disabled"])
            self.portfolio_button.state(["!disabled"])
            if not result.ok:
                self.history_text.set(f"Historical research stopped: {result.error}. Saved practice history is preserved.")
            elif kind == "download":
                self.history_text.set("History saved. Bars received / missing hours: " + "; ".join(f"{s}: {v['bars_received']}/{v.get('missing_expected_hours', v['internal_missing_hours'])}" for s, v in result.value["coins"].items()) + ". Invalid or short candles are excluded. Select Train and evaluate saved history next.")
            elif kind == "portfolio":
                self.portfolio_text.set(swing_backtest.describe(result.value))
            else:
                self.load_history_report()
        self._cancel_history = run_in_background(self, work, done)

    def _poll(self):
        self._timer = None
        if self._closed:
            return
        try:
            capture = snapshot(self.recorder.path)
            age = f" | last status {max(0, (now_ms() - capture['at']) // 1000)}s ago" if capture.get("at") else ""
            state = "This window: recording" if self.recorder.running else "This window: stopped"
            data_age = (f" | last data {max(0, (now_ms() - capture['last_received']) // 1000)}s ago"
                        if capture.get("last_received") else " | no recorded market events yet")
            self.capture_text.set(self.recorder.error or f"{state} | {capture['message']} | {capture.get('events', 0):,} events | "
                                  f"{capture.get('bytes', 0) / 1048576:.1f} MiB | gaps {capture.get('counts', {}).get('GAP', 0)}{age}{data_age}")
        except Exception as exc:
            self.capture_text.set(f"Capture status unavailable: {exc}")
        try:
            saved = background.status(self.collect_dir)
            size = saved.get("storage_bytes", 0) / 1048576
            events = saved.get("completed_events", 0) + saved.get("segment_events", 0)
            age = f"last data {max(0, (now_ms() - saved['last_received']) // 1000)}s ago" if saved.get("last_received") else "no market events recorded yet"
            prefix = "Heartbeat old — status unconfirmed" if saved.get("active") and saved.get("stale") else "Running independently" if saved.get("active") else "Stopped"
            self.background_text.set(f"{prefix} | {saved['message']} | {events:,} events | {size:.1f} MiB | {age}. Background collection continues after the interface closes.")
        except Exception as exc:
            self.background_text.set(f"Background status unavailable: {exc}")
        self._timer = self.after(2000, self._poll)

    def _destroy(self, event=None):
        if event is not None and event.widget is not self:
            return
        self.close()

    def close(self):
        self._closed = True
        self.recorder.stop()
        if self._cancel_history is not None:
            self._cancel_history()
            self._cancel_history = None
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
