"""Forward-only, public-data practice agent. No account or live-order path."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.decimals import dec
from crypto_research.errors import ConfigError, TransportError
from crypto_research.market_universe import SYMBOLS
from crypto_research.timeutil import now_ms

from . import learning, market_signals, prediction, research_costs, swing

BUDGET = Decimal("200")
POSITION_LIMIT = Decimal("50")
FEE = Decimal("0.004")
SLIPPAGE = Decimal("0.001")
HOUR = 3_600_000
MINUTE = 60_000
VERSION = "hourly-trend-learning-v3"


def initial_state() -> dict:
    return {"version": VERSION, "active": False, "cash": "200", "positions": {},
            "last_bars": {}, "last_check": 0, "equity": "200", "fees": "0",
            "message": "Ready. Start practice to watch current public prices.", "activity": [],
            "learning": learning.initial_learning(), "market_trial": None}


@dataclass(frozen=True)
class Observation:
    symbol: str
    bar_time: int
    fast: Decimal
    slow: Decimal
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    received_at: int
    market: dict | None = None


def observe(client, symbol: str, at: int, *, clock=None) -> Observation:
    """Use 50 consecutive, completed hourly bars and a fresh public quote."""
    if client.symbol_status(symbol) != "TRADING":
        raise ValueError(f"{symbol}: market is not open for trading")
    bars = client.klines(symbol, "1h", limit=60)
    closed = [b for b in bars if b.close_time < at][-50:]
    if len(closed) != 50:
        raise ValueError(f"{symbol}: need 50 completed hours of prices")
    if not 0 <= at - closed[-1].close_time <= HOUR + MINUTE:
        raise ValueError(f"{symbol}: hourly prices are out of date")
    for index, bar in enumerate(closed):
        if (bar.symbol != symbol or bar.interval != "1h" or dec(bar.close) <= 0
                or bar.close_time != bar.open_time + HOUR - 1
                or (index and bar.open_time != closed[index - 1].open_time + HOUR)):
            raise ValueError(f"{symbol}: incomplete or invalid price history")
    sample_clock = clock or (lambda: at)
    market = market_signals.collect(client, symbol, at, clock=sample_clock)
    market.update(market_signals.regime(closed))
    quote = client.book_ticker(symbol)
    bid, ask = dec(quote.bid), dec(quote.ask)
    bid_size, ask_size = dec(quote.bid_size or 0), dec(quote.ask_size or 0)
    if (quote.symbol != symbol or not 0 < bid <= ask
            or (ask - bid) / bid > Decimal("0.01")
            or bid_size <= 0 or ask_size <= 0
            or abs(sample_clock() - quote.source_received_at) > MINUTE):
        raise ValueError(f"{symbol}: price quote is stale, wide or unavailable")
    return Observation(symbol, closed[-1].close_time,
                       sum((dec(b.close) for b in closed[-20:]), Decimal(0)) / 20,
                       sum((dec(b.close) for b in closed), Decimal(0)) / 50,
                       bid, ask, bid_size, ask_size, quote.source_received_at, market)


class PracticeAgent:
    """One persistent $200 wallet. SQLite serializes checks and stop requests."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS wallet (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS activity (id INTEGER PRIMARY KEY, at INTEGER, body TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS market_samples (id INTEGER PRIMARY KEY, at INTEGER, symbol TEXT, body TEXT)")
            db.execute("INSERT OR IGNORE INTO wallet VALUES (1, ?)", (json.dumps(initial_state()),))
            prediction.initialize(db)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def snapshot(self) -> dict:
        with self._connect() as db:
            state = self._read(db)
            state["activity"] = [json.loads(r[0]) for r in db.execute(
                "SELECT body FROM activity ORDER BY id DESC LIMIT 100")]
            state["prediction"] = prediction.summary(db)
            try:
                state["swing"] = swing.summary(db, research_costs.load(self.path.parent / "research_costs.json"))
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                state["swing"] = {"error": str(exc)[:160]}
            return state

    def set_active(self, active: bool) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db)
            state["active"] = bool(active)
            state["control_revision"] = state.get("control_revision", 0) + 1
            state.pop("retry_error", None)
            state["message"] = ("Watching public prices. Practice only." if active else
                                "Paused. Pretend holdings are kept; no new decisions.")
            self._save(db, state)
        return self.snapshot()

    def _read(self, db):
        state = json.loads(db.execute("SELECT body FROM wallet WHERE id=1").fetchone()[0])
        if state["version"] == "hourly-trend-v1":
            state["learning"] = learning.initial_learning()
        if state["version"] in ("hourly-trend-v1", "hourly-trend-learning-v2"):
            state["version"] = VERSION
        if state["version"] != VERSION:
            raise ValueError("Practice rule version changed; use the matching saved wallet version")
        state.setdefault("market_trial", None)
        state.setdefault("prediction_warning", None)
        return state

    @staticmethod
    def _save(db, state):
        db.execute("UPDATE wallet SET body=? WHERE id=1", (json.dumps(state),))

    @staticmethod
    def _record(db, at, action, symbol, reason, **details):
        record = dict(at=at, action=action, symbol=symbol, reason=reason, **details)
        db.execute("INSERT INTO activity(at,body) VALUES (?,?)", (at, json.dumps(record)))

    def check(self, *, client=None, clock=now_ms, retry_data_errors=False) -> dict:
        """Fetch outside the transaction; recheck stop state before any simulation."""
        state = self.snapshot()
        if not state["active"] or clock() - state["last_check"] < MINUTE:
            return state
        revision = state.get("control_revision", 0)
        collecting = True
        try:
            public = client or BinanceUSPublicClient()
            observations = [observe(public, symbol, clock(), clock=clock) for symbol in SYMBOLS]
            at = clock()
            # A slow fetch must not turn an older quote into an actionable price.
            if any(at - o.bar_time > HOUR + MINUTE or abs(at - o.received_at) > MINUTE
                   for o in observations):
                raise ValueError("Prices became stale while checking")
            collecting = False
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                state = self._read(db)
                if (not state["active"] or state.get("control_revision", 0) != revision
                        or at - state["last_check"] < MINUTE):
                    return self.snapshot()
                state.pop("retry_error", None)
                for observation in observations:
                    db.execute("INSERT INTO market_samples(at,symbol,body) VALUES (?,?,?)",
                               (at, observation.symbol, json.dumps(observation.market)))
                self._compare_market(state, observations, at)
                decisions = self._apply(db, state, observations, at)
                if state["active"]:
                    self._learn(db, state, observations, at)
                db.execute("SAVEPOINT prediction_work")
                try:
                    prediction.advance(db, observations, at, decisions, fee=FEE, slippage=SLIPPAGE)
                    state["prediction_warning"] = None
                except Exception as exc:
                    db.execute("ROLLBACK TO prediction_work")
                    state["prediction_warning"] = f"Prediction learning paused: {type(exc).__name__}: {str(exc)[:160]}"
                finally:
                    db.execute("RELEASE prediction_work")
                db.execute("SAVEPOINT swing_work")
                try:
                    profile = research_costs.load(self.path.parent / "research_costs.json")
                    swing.advance(db, observations, at, decisions, profile)
                    state["swing_warning"] = None
                except Exception as exc:
                    db.execute("ROLLBACK TO swing_work")
                    state["swing_warning"] = f"Swing research paused: {type(exc).__name__}: {str(exc)[:160]}"
                finally:
                    db.execute("RELEASE swing_work")
                self._save(db, state)
        except Exception as exc:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                state = self._read(db)
                if state["active"] and state.get("control_revision", 0) == revision:
                    retry = retry_data_errors and collecting and isinstance(exc, (ConfigError, TransportError, ValueError, OSError))
                    state["active"] = bool(retry)
                    state["message"] = ("Waiting for fresh market data; automatic retry: " if retry else "Paused: ") + f"{type(exc).__name__}: {str(exc)[:200]}"
                    if retry:
                        state["retry_error"] = {"at": clock(), "message": state["message"]}
                    else:
                        self._record(db, clock(), "PAUSE", "", state["message"])
                    self._save(db, state)
        return self.snapshot()

    def _learn(self, db, state, observations, at):
        def make_wallet():
            wallet = initial_state()
            wallet.pop("learning")
            wallet.pop("activity")
            return wallet

        def simulate(wallet, margin, completed):
            events = []
            def record(action, symbol, reason, details):
                events.append((action, details))
            trial = json.loads(json.dumps(wallet))
            try:
                self._apply(None, trial, observations, at, margin=margin, recorder=record)
            except ValueError as exc:
                wallet["active"] = False
                wallet["message"] = f"Shadow trial stopped: {str(exc)[:200]}"
            else:
                wallet.update(trial)
                for action, details in events:
                    completed(action, details)

        learning.advance(state["learning"], observations, at, make_wallet=make_wallet, simulate=simulate,
                         record=lambda action, reason, **details: self._record(db, at, action, "", reason, **details))

    def _compare_market(self, state, observations, at):
        """Matched forward comparison, isolated from both wallet and learner."""
        if not all(market_signals.usable(o.market or {}, at) for o in observations):
            if state["market_trial"]:
                state["market_trial"]["message"] = "Comparison waiting for complete, fresh market samples."
            return
        comparison = json.loads(json.dumps(state["market_trial"]))
        if comparison is None:
            wallet = json.loads(json.dumps(state))
            for key in ("learning", "activity", "market_trial"):
                wallet.pop(key, None)
            start = dec(wallet["cash"]) + sum((dec(p["quantity"]) * o.bid * (1 - SLIPPAGE) * (1 - FEE)
                        for o in observations if (p := wallet["positions"].get(o.symbol))), Decimal(0))
            if start <= 0:
                return
            comparison = {"version": 1, "started_at": at, "starting_value": str(start), "checks": 0,
                          "margin": str(learning.entry_margin(state)), "wallets": {
                              key: {"wallet": json.loads(json.dumps(wallet)), "exits": 0,
                                    "peak": str(start), "drawdown": "0", "return_pct": "0"}
                              for key in ("reference", "signals")}}
        try:
            for key, trial in comparison["wallets"].items():
                wallet = trial["wallet"]
                if wallet["active"]:
                    def record(action, symbol, reason, details, trial=trial):
                        trial["exits"] += int(action == "SELL")
                    gates = ({o.symbol: market_signals.gate(o, at, min(POSITION_LIMIT, dec(wallet["cash"])))
                              for o in observations} if key == "signals" else None)
                    self._apply(None, wallet, observations, at, margin=dec(comparison["margin"]),
                                recorder=record, entry_gate=gates)
                equity = dec(wallet["equity"])
                trial["peak"] = str(max(dec(trial["peak"]), equity))
                trial["drawdown"] = str(max(dec(trial["drawdown"]), 1 - equity / dec(trial["peak"])))
                trial["return_pct"] = str((equity / dec(comparison["starting_value"]) - 1) * 100)
        except ValueError as exc:
            if state["market_trial"]:
                state["market_trial"]["message"] = f"Comparison check skipped: {str(exc)[:120]}"
            return
        comparison["checks"] += 1
        comparison["last_check"] = at
        comparison["message"] = "Forward comparison only; these signals cannot change the current agent's rule."
        state["market_trial"] = comparison

    def _apply(self, db, state, observations, at, *, margin=None, recorder=None, entry_gate=None):
        decisions = []
        margin = learning.entry_margin(state) if margin is None else margin
        cash = dec(state["cash"])
        equity = cash + sum((dec(p["quantity"]) * o.bid * (1 - SLIPPAGE) * (1 - FEE)
                            for o in observations if (p := state["positions"].get(o.symbol))), Decimal(0))
        halt = equity <= BUDGET * Decimal("0.8")
        eligible = [o for o in observations if o.symbol not in state["positions"]
                    and o.bar_time > state["last_bars"].get(o.symbol, 0)
                    and o.fast > o.slow * (1 + margin)
                    and (entry_gate is None or entry_gate[o.symbol][0])]
        # Share existing cash among simultaneous signals, without borrowing or
        # increasing the position cap. Keep any sub-cent remainder as cash.
        allocation = min(POSITION_LIMIT, (cash / len(eligible)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)) if eligible else POSITION_LIMIT
        for o in observations:
            planned_spend = min(allocation, cash)
            position = state["positions"].get(o.symbol)
            fresh_bar = o.bar_time > state["last_bars"].get(o.symbol, 0)
            reason = "Waiting for a new completed hour."
            action = "WAIT"
            details = {}
            if position:
                entry = dec(position["entry"])
                reason = ("Budget loss limit reached." if halt else
                          "Price fell 5% below entry." if o.bid <= entry * Decimal("0.95") else
                          "Price rose 10% above entry." if o.bid >= entry * Decimal("1.10") else
                          "20-hour average fell below 50-hour average." if o.fast <= o.slow else "")
                if reason:
                    quantity = dec(position["quantity"])
                    if quantity > o.bid_size:
                        raise ValueError(f"{o.symbol}: not enough displayed buyers for a pretend sale")
                    price = o.bid * (1 - SLIPPAGE)
                    gross = quantity * price
                    fee = gross * FEE
                    cash += gross - fee
                    state["fees"] = str(dec(state["fees"]) + fee)
                    cost = dec(position.get("cost", str(quantity * entry * (1 + FEE))))
                    del state["positions"][o.symbol]
                    action = "SELL"
                    details = dict(quantity=str(quantity), price=str(price), fee=str(fee), proceeds=str(gross - fee),
                                   cost=str(cost), realized_pnl=str(gross - fee - cost))
                else:
                    reason = "Holding: trend remains up and price limits have not been reached."
            elif fresh_bar and not halt:
                if entry_gate is not None and not entry_gate[o.symbol][0]:
                    reason = "Trial entry withheld: " + entry_gate[o.symbol][1]
                elif o.fast > o.slow * (1 + margin):
                    spend = planned_spend
                    if spend >= Decimal("10"):
                        price = o.ask * (1 + SLIPPAGE)
                        quantity = spend / ((1 + FEE) * price)
                        if quantity > o.ask_size:
                            raise ValueError(f"{o.symbol}: not enough displayed sellers for a pretend purchase")
                        fee = quantity * price * FEE
                        cash -= spend
                        state["fees"] = str(dec(state["fees"]) + fee)
                        state["positions"][o.symbol] = dict(quantity=str(quantity), entry=str(price), cost=str(spend))
                        action, reason = "BUY", f"20-hour average is over {margin * 100}% above 50-hour average."
                        details = dict(quantity=str(quantity), price=str(price), fee=str(fee), spend=str(spend))
                    else:
                        reason = "Waiting: allocated cash is below the $10 practice minimum."
                else:
                    reason = "Waiting: shorter price average is not sufficiently above the longer average."
            state["last_bars"][o.symbol] = max(o.bar_time, state["last_bars"].get(o.symbol, 0))
            if recorder is None:
                learning.actual_outcome(state["learning"], action, details)
                self._record(db, at, action, o.symbol, reason, fast=str(o.fast), slow=str(o.slow),
                             bid=str(o.bid), ask=str(o.ask), bar_time=o.bar_time,
                             entry_margin=str(margin), rule_version=VERSION,
                             entry_allocation=str(planned_spend),
                             evidence=market_signals.explanation(o, at, FEE, SLIPPAGE, planned_spend), **details)
            else:
                recorder(action, o.symbol, reason, details)
            decisions.append({"symbol": o.symbol, "action": action, "reason": reason})
        state["cash"] = str(cash)
        state["equity"] = str(cash + sum((dec(p["quantity"]) * o.bid * (1 - SLIPPAGE) * (1 - FEE)
                                          for o in observations if (p := state["positions"].get(o.symbol))), Decimal(0)))
        state["last_check"] = at
        state["message"] = "Last check completed. Watching for the next opportunity."
        if halt:
            state["active"] = False
            state["message"] = "Stopped at the $40 loss threshold. All pretend holdings were sold. Losses can exceed $40 after price gaps."
        return decisions
