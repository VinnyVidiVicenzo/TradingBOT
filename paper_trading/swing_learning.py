"""Prospective swing learning, with frozen candidates and isolated paper trials.

Hourly training examples overlap. Validation scores select nonoverlapping
24-hour targets per coin and group prediction errors by seven-day blocks.
No function in this module changes the owning wallet or its frozen model.
"""
from __future__ import annotations

from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3

from crypto_research.decimals import dec
from crypto_research.market_universe import SYMBOLS
from . import historical, research_costs

VERSION = "swing-forward-learning-v1"
HOUR, DAY, GRACE = historical.HOUR, 24 * historical.HOUR, 300_000
MIN_TRAIN = 100
TRAIN_DAYS = 7
TRIAL_DAYS = 35
MIN_SCORED = 100
MIN_EXITS = 10


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def initial(config):
    return {"version": VERSION, "config_id": config["sha256"], "candidate": list(config["weights"]),
            "labels": 0, "positives": 0, "training_days": [], "next_freeze": MIN_TRAIN,
            "next_id": 1, "trial": None, "last_validation": None, "qualified": 0,
            "latest_predictions": {}, "message": "Collecting new swing outcomes; the active model stays fixed."}


def _validate(q, at):
    values = q.get("features")
    return (isinstance(values, list) and len(values) == len(historical.FEATURES)
            and all(type(x) in (int, float) and math.isfinite(x) and abs(x) <= 1 for x in values)
            and 0 <= at - q["received_at"] <= 60_000 and q["bar_time"] % HOUR == 0
            and HOUR <= at - q["bar_time"] <= 2 * HOUR + 60_000
            and 0 < dec(q["bid"]) <= dec(q["ask"]))


def _net(bid, row):
    fee, slip = dec(row["fee"]), dec(row["slippage"])
    return dec(bid) * (1 - slip) * (1 - fee) / (dec(row["ask"]) * (1 + slip) * (1 + fee)) - 1


def _train(weights, x, label, count):
    error = historical.probability(weights, x) - label
    rate = 0.05 / math.sqrt(1 + count / 1000)
    return [max(-10.0, min(10.0, w - rate * (error * v + (0.001 * w if i else 0))))
            for i, (w, v) in enumerate(zip(weights, x))]


def _metrics(wallet):
    return {"return_pct": float((dec(wallet["equity"]) / dec(wallet["initial"]) - 1) * 100),
            "drawdown_pct": float(dec(wallet["drawdown"]) * 100), "exits": wallet["exits"],
            "fees": wallet["fees"], "halted": wallet["halted"]}


def _shadow(db, study, state, config, quotes, at):
    from . import swing_strategy as policy
    trial = state["trial"]
    if not trial or at <= trial["frozen_at"]:
        return
    if trial["started_at"] is None and not trial["entries_allowed"]:
        return
    if trial["started_at"] is None:
        trial["started_at"], trial["ends_at"] = at, at + TRIAL_DAYS * DAY
        trial["wallets"] = {}
        for name in ("reference", "candidate", "stress", "buy_hold"):
            selected = json.loads(_json(config))
            if name != "reference":
                selected["weights"] = list(trial["weights"])
            if name == "stress":
                profile = selected["profile"]
                profile.update(verification="assumed", source="Twice-cost paper stress")
                for rates in profile["fees"].values():
                    for side in ("maker", "taker"):
                        rates[side] = str(min(dec("0.1"), 2 * dec(rates[side])))
                profile["slippage"] = str(min(dec("0.1"), 2 * dec(profile["slippage"])))
                selected["profile"] = research_costs.validate(profile)
            wallet = policy.initial(trial["capital"], selected)
            wallet["active"] = True
            trial["wallets"][name] = wallet
        trial["benchmark_complete"] = True
    if at > trial["ends_at"]:
        return
    if trial["last_shadow_at"] is not None:
        trial["max_gap_ms"] = max(trial["max_gap_ms"], at - trial["last_shadow_at"])
    trial["last_shadow_at"] = at
    for name, wallet in trial["wallets"].items():
        current = {s: dict(q) for s, q in quotes.items()}
        for q in current.values():
            q["score"] = historical.probability(wallet["config"]["weights"], q["features"])
            if name == "stress":
                mid = (dec(q["ask"]) + dec(q["bid"])) / 2
                q["bid"] = str(mid - 2 * (mid - dec(q["bid"])))
                q["ask"] = str(mid + 2 * (dec(q["ask"]) - mid))
        if name == "buy_hold":
            if not trial.get("benchmark_started"):
                fee, slip = policy.costs(wallet["config"])
                spend = dec(wallet["initial"]) / len(SYMBOLS)
                for symbol, q in current.items():
                    price = dec(q["ask"]) * (1 + slip)
                    quantity = spend / (price * (1 + fee))
                    if quantity > dec(q["ask_size"]):
                        trial["benchmark_complete"] = False
                        continue  # preserve cash instead of inventing a fill
                    wallet["cash"] = str(dec(wallet["cash"]) - spend)
                    wallet["fees"] = str(dec(wallet["fees"]) + quantity * price * fee)
                    wallet["positions"][symbol] = {"quantity": str(quantity), "entry": str(price), "cost": str(spend), "at": at}
                trial["benchmark_started"] = True
            policy.mark(wallet, current)
        elif wallet["active"]:
            events = policy.advance(wallet, current, at, entries_allowed=trial["entries_allowed"])
            for event in events:
                if event["action"] != "WAIT":
                    db.execute("INSERT INTO swing_learning_events(study,at,kind,body) VALUES(?,?,?,?)",
                               (study, at, "SHADOW_TRADE", _json({"trial_id": trial["id"], "wallet": name, **event})))


def _score(trial, row, at):
    if (row["trial_id"] != trial["id"] or trial["started_at"] is None
            or not trial["started_at"] <= row["predicted_at"] < at <= trial["ends_at"]
            or row["predicted_at"] < trial["next_scored_at"].get(row["symbol"], 0)):
        return
    trial["next_scored_at"][row["symbol"]] = at
    trial["scored"] += 1
    trial["scored_coins"][row["symbol"]] = trial["scored_coins"].get(row["symbol"], 0) + 1
    day = (at - trial["started_at"]) // DAY
    scores = trial["days"].setdefault(str(day), {"n": 0, "advantage": 0.0})
    scores["n"] += 1
    scores["advantage"] += (row["reference_probability"] - row["outcome"]) ** 2 - (row["trial_probability"] - row["outcome"]) ** 2


def weekly_evidence(trial):
    blocks = {}
    for day, values in trial["days"].items():
        day = int(day)
        if day < 1:
            continue
        blocks.setdefault((day - 1) // 7, []).append((day, values["advantage"] / values["n"]))
    means = [sum(v for _, v in days) / 7 for days in blocks.values() if len(days) == 7]
    n = len(means)
    lower = None
    if n >= 4:
        mean = sum(means) / n
        se = math.sqrt(sum((v - mean) ** 2 for v in means) / (n - 1) / n)
        critical = {4: 3.182, 5: 2.776}.get(n, 2.571)
        lower = mean - critical * se
    return {"complete_weeks": n, "lower_advantage": lower}


def _finish(trial):
    metrics = {name: _metrics(wallet) for name, wallet in (trial["wallets"] or {}).items()}
    evidence = weekly_evidence(trial)
    reasons = []
    if trial["scored"] < MIN_SCORED or min((trial["scored_coins"].get(s, 0) for s in SYMBOLS), default=0) < 10:
        reasons.append("not enough nonoverlapping outcomes for every coin")
    if evidence["complete_weeks"] < 4 or evidence["lower_advantage"] is None or evidence["lower_advantage"] <= 0.005:
        reasons.append("weekly prediction improvement is not established")
    if trial["max_gap_ms"] > 2 * HOUR or not trial["last_shadow_at"] or trial["ends_at"] - trial["last_shadow_at"] > 120_000:
        reasons.append("comparison has missing observation coverage")
    if not trial["benchmark_complete"]:
        reasons.append("buy/hold benchmark could not obtain its estimated entry liquidity")
    if not trial["costs_valid"]:
        reasons.append("cost profile became stale or changed")
    if metrics:
        candidate, reference, stress, hold = (metrics[n] for n in ("candidate", "reference", "stress", "buy_hold"))
        if candidate["exits"] < MIN_EXITS or candidate["return_pct"] <= max(0, reference["return_pct"], hold["return_pct"]) + 1:
            reasons.append("candidate has not beaten matched references after costs with enough completed trades")
        if stress["return_pct"] <= 0 or candidate["drawdown_pct"] > max(reference["drawdown_pct"], 1.0):
            reasons.append("cost stress or drawdown criteria failed")
    else:
        reasons.append("matched paper portfolios did not start")
    return {"id": trial["id"], "qualified": not reasons, "reasons": reasons, "scored": trial["scored"],
            "started_at": trial["started_at"], "ends_at": trial["ends_at"], "metrics": metrics, **evidence,
            "meaning": "Research review only; the active swing model is unchanged."}


def record(path, config, quotes, at, decisions, capital, *, entries_allowed=True):
    from . import swing_strategy as policy
    if set(quotes) != set(SYMBOLS) or not all(_validate(q, at) for q in quotes.values()):
        raise ValueError("Learning needs fresh valid features and quotes for all five coins")
    path = Path(path)
    if path.exists() and path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError("Swing learning storage budget reached; history was preserved")
    path.parent.mkdir(parents=True, exist_ok=True)
    study = config["sha256"]
    with closing(sqlite3.connect(path, timeout=10)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS swing_learning_state(study TEXT PRIMARY KEY,body TEXT)")
        db.execute("CREATE TABLE IF NOT EXISTS swing_examples(id INTEGER PRIMARY KEY,study TEXT,symbol TEXT,bar_time INTEGER,at INTEGER,due INTEGER,expires INTEGER,status TEXT,body TEXT,UNIQUE(study,symbol,bar_time))")
        db.execute("CREATE INDEX IF NOT EXISTS swing_examples_pending ON swing_examples(study,status,due)")
        db.execute("CREATE TABLE IF NOT EXISTS swing_learning_models(study TEXT,model_id TEXT,at INTEGER,body TEXT,PRIMARY KEY(study,model_id))")
        db.execute("CREATE TABLE IF NOT EXISTS swing_learning_events(id INTEGER PRIMARY KEY,study TEXT,at INTEGER,kind TEXT,body TEXT)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT body FROM swing_learning_state WHERE study=?", (study,)).fetchone()
        state = json.loads(row[0]) if row else initial(config)
        if state["version"] != VERSION:
            raise ValueError("Saved swing learner version differs")
        if (len(state["candidate"]) != len(historical.FEATURES)
                or any(type(w) not in (int, float) or not math.isfinite(w) or abs(w) > 10 for w in state["candidate"])):
            raise ValueError("Saved swing learning weights are invalid")
        if at < state.get("last_observed_at", 0):
            raise ValueError("Learning clock moved backwards")
        state["last_observed_at"] = at
        db.execute("UPDATE swing_examples SET status='expired' WHERE study=? AND status='pending' AND expires<?", (study, at))
        trial = state["trial"]
        if trial:
            trial["entries_allowed"] = entries_allowed
            if trial["started_at"] is not None:
                trial["costs_valid"] = trial["costs_valid"] and entries_allowed
            _shadow(db, study, state, config, quotes, at)
        pending = db.execute("SELECT id,symbol,due,body FROM swing_examples WHERE study=? AND status='pending' ORDER BY due,id", (study,)).fetchall()
        for key, symbol, due, body in pending:
            sample, q = json.loads(body), quotes[symbol]
            sample["min_bid"] = str(min(dec(sample["min_bid"]), dec(q["bid"])))
            sample["observed_checks"] += 1
            status = "pending"
            if due <= q["received_at"] <= at:
                net = _net(q["bid"], sample)
                sample.update(outcome=int(net > 0), net_return=str(net), outcome_at=q["received_at"],
                              worst_observed_net=str(_net(sample["min_bid"], sample)))
                if trial:
                    _score(trial, sample, q["received_at"])
                state["candidate"] = _train(state["candidate"], sample["features"], sample["outcome"], state["labels"])
                state["labels"] += 1
                state["positives"] += sample["outcome"]
                day = at // DAY
                if day not in state["training_days"]:
                    state["training_days"].append(day)
                status = "resolved"
            db.execute("UPDATE swing_examples SET body=?,status=? WHERE id=?", (_json(sample), status, key))
        if trial and trial["ends_at"] is not None and at >= trial["ends_at"]:
            validation = _finish(trial)  # assess ONCE at the fixed deadline, never on every good result
            state["last_validation"] = validation
            state["qualified"] += int(validation["qualified"])
            db.execute("INSERT INTO swing_learning_events(study,at,kind,body) VALUES(?,?,?,?)", (study, at, "VALIDATION", _json(validation)))
            state["trial"] = None
            state["next_freeze"] = state["labels"] + MIN_TRAIN
            trial = None
        if (trial is None and entries_allowed and state["labels"] >= state["next_freeze"]
                and len(state["training_days"]) >= TRAIN_DAYS):
            model_id = "candidate-" + str(state["next_id"])
            state["next_id"] += 1
            trial = {"id": model_id, "weights": list(state["candidate"]), "frozen_at": at,
                     "training_labels": state["labels"], "capital": str(capital), "started_at": None, "ends_at": None,
                     "wallets": None, "scored": 0, "scored_coins": {}, "next_scored_at": {}, "days": {},
                     "last_shadow_at": None, "max_gap_ms": 0, "benchmark_complete": False,
                     "costs_valid": True, "entries_allowed": entries_allowed}
            state["trial"] = trial
            db.execute("INSERT INTO swing_learning_models VALUES(?,?,?,?)", (study, model_id, at, _json({"version": VERSION, "features": historical.FEATURES, "config": config, **trial})))
        actions = {d["symbol"]: d for d in decisions}
        for symbol, q in quotes.items():
            probability = historical.probability(state["candidate"], q["features"])
            state["latest_predictions"][symbol] = probability
            if not entries_allowed or db.execute("SELECT 1 FROM swing_examples WHERE study=? AND symbol=? AND bar_time=?", (study, symbol, q["bar_time"])).fetchone():
                continue
            sample = {"symbol": symbol, "predicted_at": at, "due": at + DAY, "features": list(q["features"]),
                      "ask": q["ask"], "min_bid": q["bid"], "observed_checks": 1,
                      "fee": str(research_costs.fee(config["profile"])), "slippage": config["profile"]["slippage"],
                      "cost_profile": config["profile"], "reference_probability": historical.probability(config["weights"], q["features"]),
                      "candidate_probability": probability, "training_labels_at_prediction": state["labels"],
                      "trial_id": trial["id"] if trial else None,
                      "trial_probability": historical.probability(trial["weights"], q["features"]) if trial else None,
                      "action": actions.get(symbol, {}).get("action", "WAIT"),
                      "reason": actions.get(symbol, {}).get("reason", "No new wallet decision at this check")}
            db.execute("INSERT INTO swing_examples(study,symbol,bar_time,at,due,expires,status,body) VALUES(?,?,?,?,?,?,'pending',?)",
                       (study, symbol, q["bar_time"], at, at + DAY, at + DAY + GRACE, _json(sample)))
        state["message"] = ("Frozen candidate is in a separate 35-day comparison; active wallet/model unchanged." if trial else
                            "Training on newly resolved 24-hour outcomes; active wallet/model unchanged.")
        if not entries_allowed:
            state["message"] = "New learning examples paused by stale/changed fees; saved pending examples can still resolve."
        db.execute("INSERT OR REPLACE INTO swing_learning_state VALUES(?,?)", (study, _json(state)))


def summary(path, config):
    path = Path(path)
    if not path.exists():
        return {"waiting": True, "message": "Learning starts on the next successful swing check. No history is backfilled."}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT body FROM swing_learning_state WHERE study=?", (config["sha256"],)).fetchone()
        if row is None:
            return {"waiting": True, "message": "Waiting for the first observation under this frozen model."}
        state = json.loads(row[0])
        counts = dict(db.execute("SELECT status,count(*) FROM swing_examples WHERE study=? GROUP BY status", (config["sha256"],)))
        due = db.execute("SELECT min(due) FROM swing_examples WHERE study=? AND status='pending'", (config["sha256"],)).fetchone()[0]
        trial = state["trial"]
        return {"message": state["message"], "counts": counts, "labels": state["labels"],
                "training_days": len(state["training_days"]), "next_due": due, "qualified": state["qualified"],
                "latest_predictions": state["latest_predictions"], "last_validation": state["last_validation"],
                "trial": ({"id": trial["id"], "frozen_at": trial["frozen_at"], "started_at": trial["started_at"],
                           "ends_at": trial["ends_at"], "scored": trial["scored"], **weekly_evidence(trial),
                           "metrics": {name: _metrics(w) for name, w in (trial["wallets"] or {}).items()}} if trial else None)}
