"""Frozen daily swing policy and an isolated, forward-only pretend wallet."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.decimals import dec
from crypto_research.errors import ConfigError, TransportError
from crypto_research.market_universe import SYMBOLS
from crypto_research.timeutil import now_ms
from . import historical, research_costs, swing_learning

HOUR = historical.HOUR
VERSION = "daily-swing-practice-v1"


def config_from_report(report):
    if report["version"] != historical.VERSION or tuple(report["features"]) != historical.FEATURES:
        raise ValueError("Historical model feature version does not match")
    fold = report["tracks"]["24h"]["folds"][-1]  # fixed choice, never the best test result
    weights = fold["weights"]
    if (len(weights) != len(historical.FEATURES) or any(not math.isfinite(w) or abs(w) > 10 for w in weights)
            or fold["training_last_label"] >= fold["test_start"]):
        raise ValueError("Invalid or nonchronological frozen model")
    result = {"version": VERSION, "weights": list(weights), "features": list(historical.FEATURES),
              "report_id": report["id"], "dataset_sha256": report["dataset_sha256"],
              "training_last_label": fold["training_last_label"], "threshold": 0.6,
              "hold_hours": 24, "stop": "0.05", "target": "0.10", "loss_limit": "0.20",
              "position_fraction": "0.25", "profile": research_costs.validate(report["cost_profile"])}
    result["sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def initial(capital, config):
    capital = dec(capital)
    if not 10 <= capital <= dec("1000000000"):
        raise ValueError("Practice starting amount must be between $10 and $1 billion")
    return {"version": VERSION, "config": config, "initial": str(capital), "cash": str(capital),
            "positions": {}, "equity": str(capital), "peak": str(capital), "drawdown": "0",
            "fees": "0", "realized_pnl": "0", "exits": 0, "wins": 0, "halted": False,
            "active": False, "last_check": 0, "last_bars": {}, "waiting": {}, "message": "Swing practice is paused."}


def costs(config):
    profile = config["profile"]
    return research_costs.fee(profile), dec(profile["slippage"])


def value(state, quotes):
    fee, slip = costs(state["config"])
    return dec(state["cash"]) + sum((dec(p["quantity"]) * dec(quotes[s]["bid"]) * (1 - slip) * (1 - fee)
                                   for s, p in state["positions"].items()), dec(0))


def mark(state, quotes):
    equity = value(state, quotes)
    state["equity"] = str(equity)
    state["peak"] = str(max(dec(state["peak"]), equity))
    state["drawdown"] = str(max(dec(state["drawdown"]), 1 - equity / dec(state["peak"])))
    if equity <= dec(state["initial"]) * (1 - dec(state["config"]["loss_limit"])):
        state["halted"] = True


def close_position(state, symbol, quote, at, reason, events):
    position = state["positions"][symbol]
    quantity = dec(position["quantity"])
    if quote.get("bid_size") is not None and quantity > dec(quote["bid_size"]):
        return False
    fee, slip = costs(state["config"])
    gross = quantity * dec(quote["bid"]) * (1 - slip)
    paid = gross * fee
    proceeds = gross - paid
    pnl = proceeds - dec(position["cost"])
    state["cash"] = str(dec(state["cash"]) + proceeds)
    state["fees"] = str(dec(state["fees"]) + paid)
    state["realized_pnl"] = str(dec(state["realized_pnl"]) + pnl)
    state["exits"] += 1
    state["wins"] += int(pnl > 0)
    del state["positions"][symbol]
    events.append({"at": at, "action": "SELL", "symbol": symbol, "reason": reason,
                   "quantity": str(quantity), "fee": str(paid), "proceeds": str(proceeds), "realized_pnl": str(pnl)})
    return True


def advance(state, quotes, at, *, entries_allowed=True):
    if set(quotes) != set(SYMBOLS):
        raise ValueError("Fresh prices for all five swing markets are required")
    config = state["config"]
    fee, slip = costs(config)
    events, exited = [], set()
    mark(state, quotes)
    blocked_exit = False
    for symbol, position in list(state["positions"].items()):
        q, entry = quotes[symbol], dec(position["entry"])
        reason = ("Portfolio loss trigger reached" if state["halted"] else
                  "Price fell 5% from entry" if dec(q["bid"]) <= entry * (1 - dec(config["stop"])) else
                  "Price rose 10% from entry" if dec(q["bid"]) >= entry * (1 + dec(config["target"])) else
                  "24-hour holding period completed" if at - position["at"] >= config["hold_hours"] * HOUR else
                  "Hourly trend reversed" if dec(q["trend_gap"]) <= 0 else None)
        if reason:
            if close_position(state, symbol, q, at, reason, events):
                exited.add(symbol)
            else:
                blocked_exit = True
    eligible = [s for s in SYMBOLS if s not in state["positions"] and s not in exited
                and quotes[s]["bar_time"] > state["last_bars"].get(s, -1)
                and quotes[s]["score"] >= config["threshold"] and dec(quotes[s]["trend_gap"]) > dec("0.002")]
    allocation = min(dec(state["initial"]) * dec(config["position_fraction"]),
                     dec(state["cash"]) / len(eligible)) if eligible else dec(0)
    # Conservative rounding prevents repeated divisions from borrowing cash.
    allocation = allocation.quantize(dec("0.00000001"), rounding="ROUND_DOWN")
    for symbol in SYMBOLS:
        q = quotes[symbol]
        fresh = q["bar_time"] > state["last_bars"].get(symbol, -1)
        if symbol in state["positions"]:
            reason = "Holding; exit checks continue each refresh"
            if blocked_exit and state["halted"]:
                reason = "Loss trigger reached; waiting for displayed buyers to exit"
        elif state["halted"]:
            reason = "Portfolio loss trigger reached; new entries stopped"
        elif blocked_exit:
            reason = "An exit needs liquidity; new entries withheld"
        elif not entries_allowed:
            reason = "Fee profile is stale or changed; refresh costs before new entries"
        elif symbol in exited:
            reason = "Exited this check; wait for another completed hour"
        elif q["score"] < config["threshold"]:
            reason = f"Model score {q['score']:.3f} needs at least {config['threshold']:.2f}"
        elif dec(q["trend_gap"]) <= dec("0.002"):
            reason = "20-hour average must be over 0.2% above the 50-hour average"
        elif not fresh:
            reason = "Entry conditions met; waiting for a new completed hour"
        elif allocation < dec(state["initial"]) * dec("0.05"):
            reason = "Available allocation is below 5% of starting practice capital"
        else:
            price = dec(q["ask"]) * (1 + slip)
            quantity = allocation / (price * (1 + fee))
            if q.get("ask_size") is not None and quantity > dec(q["ask_size"]):
                reason = "Not enough displayed sellers for the estimated purchase"
            else:
                paid = quantity * price * fee
                state["cash"] = str(dec(state["cash"]) - allocation)
                state["fees"] = str(dec(state["fees"]) + paid)
                state["positions"][symbol] = {"quantity": str(quantity), "entry": str(price), "cost": str(allocation), "at": at}
                reason = "Frozen daily model and hourly trend support this practice entry"
                events.append({"at": at, "action": "BUY", "symbol": symbol, "reason": reason,
                               "score": q["score"], "spend": str(allocation), "quantity": str(quantity), "fee": str(paid)})
        previous = state["waiting"].get(symbol, {}).get("reason")
        state["waiting"][symbol] = {"reason": reason, "score": q["score"], "trend_gap": str(q["trend_gap"]),
                                    "bar_time": q["bar_time"], "observed_at": at}
        if (fresh or reason != previous) and not any(e["symbol"] == symbol for e in events):
            events.append({"at": at, "action": "WAIT", "symbol": symbol, "reason": reason, "score": q["score"]})
        state["last_bars"][symbol] = max(q["bar_time"], state["last_bars"].get(symbol, -1))
    mark(state, quotes)
    state["last_check"] = at
    state["message"] = "Swing check completed; waiting conditions are shown per coin."
    if state["halted"]:
        state["active"] = bool(state["positions"])
        state["message"] = ("Loss trigger reached; still monitoring unresolved exits." if state["positions"] else
                            "Stopped after the portfolio loss trigger; price gaps can exceed the limit.")
    return events


def quote_from_bars(symbol, past, bid, ask, at, config, **sizes):
    values, _ = historical.features(past, symbol)
    fast = sum((dec(p["close"]) for p in past[-20:]), dec(0)) / 20
    slow = sum((dec(p["close"]) for p in past[-50:]), dec(0)) / 50
    return {"bid": str(bid), "ask": str(ask), "score": historical.probability(config["weights"], values),
            "features": list(values), "trend_gap": str(fast / slow - 1), "bar_time": past[-1]["at"], "received_at": at, **sizes}


def fetch(client, config, clock):
    result = {}
    for symbol in SYMBOLS:
        at = clock()
        if client.symbol_status(symbol) != "TRADING":
            raise ValueError(f"{symbol}: market is unavailable")
        bars = [b for b in client.klines(symbol, "1h", limit=75) if b.close_time < at][-50:]
        end = at // HOUR * HOUR
        if len(bars) != 50 or not 0 <= at - bars[-1].close_time <= HOUR + 60_000:
            raise ValueError(f"{symbol}: need fresh completed hourly history")
        past = [historical.normalize(b, symbol, bars[0].open_time, end) for b in bars]
        if any(b["at"] - a["at"] != HOUR for a, b in zip(past, past[1:])):
            raise ValueError(f"{symbol}: hourly history has gaps")
        q = client.book_ticker(symbol)
        bid, ask = dec(q.bid), dec(q.ask)
        if (q.symbol != symbol or not 0 < bid <= ask or (ask - bid) / bid > dec("0.01")
                or dec(q.bid_size or 0) <= 0 or dec(q.ask_size or 0) <= 0
                or not 0 <= clock() - q.source_received_at <= 60_000):
            raise ValueError(f"{symbol}: quote is stale, wide or unavailable")
        result[symbol] = quote_from_bars(symbol, past, bid, ask, q.source_received_at, config,
                                       bid_size=str(q.bid_size), ask_size=str(q.ask_size))
    if any(not 0 <= clock() - q["received_at"] <= 60_000 for q in result.values()):
        raise ValueError("Swing prices became stale during collection")
    return result


def reference_equity(path):
    """Read only the existing pretend wallet's reference size, without migration."""
    path = Path(path)
    if not path.exists():
        return None
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = db.execute("SELECT body FROM wallet WHERE id=1").fetchone()
        return json.loads(row[0])["equity"] if row else None
    finally:
        db.close()


class SwingPracticeAgent:
    def __init__(self, path):
        self.path = Path(path)
        self.learning_path = self.path.with_name(self.path.stem + "_learning.sqlite")
        self.learning_error = None

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS swing_wallet(id INTEGER PRIMARY KEY,body TEXT)")
                db.execute("CREATE TABLE IF NOT EXISTS swing_activity(id INTEGER PRIMARY KEY,body TEXT)")
                yield db
        finally:
            db.close()

    def initialize(self, capital, report):
        config = config_from_report(report)
        wallet = initial(capital, config)  # Validate before creating any database.
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO swing_wallet VALUES(1,?)", (json.dumps(wallet),))
        return self.snapshot()

    @staticmethod
    def read(db):
        row = db.execute("SELECT body FROM swing_wallet WHERE id=1").fetchone()
        if not row:
            raise ValueError("Initialize swing practice first")
        state = json.loads(row[0])
        if state["version"] != VERSION:
            raise ValueError("Saved swing strategy has a different version")
        return state

    @staticmethod
    def save(db, state):
        db.execute("UPDATE swing_wallet SET body=? WHERE id=1", (json.dumps(state),))

    def snapshot(self):
        if not self.path.exists():
            return None
        with self.connect() as db:
            state = self.read(db)
            state["activity"] = [json.loads(body) for (body,) in db.execute("SELECT body FROM swing_activity ORDER BY id DESC LIMIT 40")]
            try:
                state["learning"] = swing_learning.summary(self.learning_path, state["config"])
                if self.learning_error:
                    state["learning"]["error"] = self.learning_error
            except Exception as exc:
                state["learning"] = {"error": str(exc)[:180]}
            return state

    def set_active(self, active):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self.read(db)
            if active and state["halted"] and not state["positions"]:
                raise ValueError("This swing session reached its loss limit; its history must be preserved")
            state["active"] = bool(active)
            state["control_revision"] = state.get("control_revision", 0) + 1
            state.pop("retry_error", None)
            state["message"] = "Swing practice running; background checks continue with the window closed." if active else "Swing practice paused; pretend holdings are kept."
            self.save(db, state)
        return self.snapshot()

    def check(self, *, client=None, clock=now_ms, profile_path=None, retry_data_errors=False):
        state = self.snapshot()
        if not state or not state["active"] or clock() - state["last_check"] < 60_000:
            return state
        learning_work = None
        revision = state.get("control_revision", 0)
        collecting = False
        try:
            if state["config"]["training_last_label"] >= clock():
                raise ValueError("Frozen swing training is ahead of the current clock")
            collecting = True
            quotes = fetch(client or BinanceUSPublicClient(), state["config"], clock)
            collecting = False
            at = clock()
            profile = research_costs.load(Path(profile_path) if profile_path else self.path.parent / "research_costs.json")
            observed = profile["observed_at"]
            allowed = (profile["sha256"] == state["config"]["profile"]["sha256"] and observed is not None
                       and 0 <= at - observed <= 7 * 24 * HOUR)
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                state = self.read(db)
                if (not state["active"] or state.get("control_revision", 0) != revision
                        or at - state["last_check"] < 60_000):
                    return state
                state.pop("retry_error", None)
                events = advance(state, quotes, at, entries_allowed=allowed)
                for event in events:
                    db.execute("INSERT INTO swing_activity(body) VALUES(?)", (json.dumps(event),))
                self.save(db, state)
                learning_work = (state["config"], quotes, at, events, state["equity"], allowed)
        except Exception as exc:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                state = self.read(db)
                if state["active"] and state.get("control_revision", 0) == revision:
                    retry = retry_data_errors and collecting and isinstance(exc, (ConfigError, TransportError, ValueError, OSError))
                    state.update(active=bool(retry), message=("Waiting for fresh market data; automatic retry: " if retry else "Swing practice paused: ") + f"{type(exc).__name__}: {str(exc)[:180]}")
                    if retry:
                        state["retry_error"] = {"at": clock(), "message": state["message"]}
                    self.save(db, state)
        if learning_work:
            config, quotes, at, events, capital, allowed = learning_work
            try:
                swing_learning.record(self.learning_path, config, quotes, at, events, capital, entries_allowed=allowed)
                self.learning_error = None
            except Exception as exc:
                self.learning_error = f"Learning paused: {type(exc).__name__}: {str(exc)[:150]}"
        return self.snapshot()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separate frozen swing strategy; pretend wallet only")
    parser.add_argument("command", choices=("start", "check", "pause", "status"))
    parser.add_argument("--database", default="data/swing_practice.sqlite")
    parser.add_argument("--study-directory", default="data/learning_research")
    parser.add_argument("--capital", help="Starting pretend cash, used only for a new session")
    args = parser.parse_args(argv)
    agent = SwingPracticeAgent(args.database)
    if args.command == "start":
        if not agent.path.exists():
            if not args.capital:
                parser.error("A new session requires --capital; it is a practice setting, not a funding recommendation")
            agent.initialize(args.capital, historical.latest(args.study_directory))
        agent.set_active(True)
        result = agent.check()
    elif args.command == "check":
        result = agent.check()
    elif args.command == "pause":
        result = agent.set_active(False)
    else:
        result = agent.snapshot()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
