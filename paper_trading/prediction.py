"""Forward-only opportunity learning. Advisory estimates never drive orders.

One forecast per observed coin/hour, with a delayed one-hour net-return label.
Frozen challenger validation is separate from incremental candidate training.
All state is ordinary JSON/SQLite, never executable serialized model objects.
"""

from __future__ import annotations

import hashlib
import json
import math

from crypto_research.decimals import dec

from . import market_signals

HOUR = 3_600_000
GRACE = 300_000
VERSION = "opportunity-logistic-v1"
FEATURES = ("bias", "trend", "volatility", "buy_flow", "volume", "spread",
            "depth_balance", "missing_market", "ether")
TRAIN_SAMPLES = 100
VALIDATION_SAMPLES = 100
VALIDATION_TIME = 7 * 24 * HOUR
DRIFT_BLOCK = 50


def initialize(db, *, horizon=HOUR):
    if horizon not in (HOUR, 4 * HOUR, 24 * HOUR):
        raise ValueError("Unsupported prediction horizon")
    db.execute("CREATE TABLE IF NOT EXISTS prediction_state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS prediction_models (model_id TEXT PRIMARY KEY, at INTEGER, body TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS opportunities (id INTEGER PRIMARY KEY, symbol TEXT, bar_time INTEGER, "
               "at INTEGER, due INTEGER, expires INTEGER, status TEXT, body TEXT NOT NULL, UNIQUE(symbol,bar_time))")
    db.execute("CREATE INDEX IF NOT EXISTS opportunity_pending ON opportunities(status,due)")
    db.execute("CREATE TABLE IF NOT EXISTS prediction_events (id INTEGER PRIMARY KEY, at INTEGER, kind TEXT, body TEXT)")
    initial = {"version": VERSION, "horizon_ms": horizon, "candidate": [0.0] * len(FEATURES), "labels": 0, "positives": 0,
               "champion": {"id": "baseline-0", "weights": [0.0] * len(FEATURES)}, "fallback": None,
               "trial": None, "next_version": 1, "next_freeze": TRAIN_SAMPLES,
               "score": {"n": 0, "model": 0.0, "baseline": 0.0}, "regimes": {},
               "drift": [], "bad_blocks": 0, "rollbacks": 0, "promotions": 0,
               "message": "Collecting future outcomes; predictions cannot change practice trades."}
    db.execute("INSERT OR IGNORE INTO prediction_state VALUES (1,?)", (_json(initial),))
    db.execute("INSERT OR IGNORE INTO prediction_models VALUES (?,?,?)",
               ("baseline-0", 0, _json({"version": VERSION, "features": FEATURES, "weights": initial["candidate"]})))


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def _load(db):
    state = json.loads(db.execute("SELECT body FROM prediction_state WHERE id=1").fetchone()[0])
    if state["version"] != VERSION:
        raise ValueError("Saved prediction model has a different feature version")
    vectors = [state["candidate"], state["champion"]["weights"]]
    vectors.extend(state[name]["weights"] for name in ("trial", "fallback") if state[name])
    for weights in vectors:
        if len(weights) != len(FEATURES) or any(not math.isfinite(w) or abs(w) > 10 for w in weights):
            raise ValueError("Invalid saved prediction weights")
    return state


def features(observation, at):
    market = observation.market or {}
    available = market_signals.usable(market, at)
    def clip(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite opportunity feature")
        return max(-1.0, min(1.0, number))
    depth = dec(market["buy_depth"]) + dec(market["sell_depth"]) if available else dec(0)
    return [1.0, clip((observation.fast / observation.slow - 1) * 20),
            clip(dec(market.get("hourly_rms", 0)) * 25),
            clip((dec(market["buy_share"]) - dec("0.5")) * 2) if available else 0.0,
            clip((dec(market["volume_ratio"]) - 1) / 3) if market.get("volume_ratio") is not None else 0.0,
            clip((observation.ask / observation.bid - 1) * 100),
            clip((dec(market["buy_depth"]) - dec(market["sell_depth"])) / depth) if depth else 0.0,
            # Keep the saved BTC/ETH feature meanings and vector dimensions.
            # New coins use neutral identity, never Bitcoin's -1 encoding.
            0.0 if available else 1.0, {"ETHUSD": 1.0, "BTCUSD": -1.0}.get(observation.symbol, 0.0)]


def probability(weights, values):
    if len(weights) != len(FEATURES) or len(values) != len(FEATURES):
        raise ValueError("Prediction feature dimensions changed")
    if any(not math.isfinite(v) for v in (*weights, *values)):
        raise ValueError("Non-finite prediction input")
    z = max(-30.0, min(30.0, sum(w * x for w, x in zip(weights, values))))
    return 1.0 / (1.0 + math.exp(-z))


def train(weights, values, label, count):
    error = probability(weights, values) - label
    rate = 0.05 / math.sqrt(1 + count / 1000)
    return [max(-10.0, min(10.0, w - rate * (error * x + (0.001 * w if i else 0))))
            for i, (w, x) in enumerate(zip(weights, values))]


def advance(db, observations, at, decisions, *, fee, slippage, horizon=HOUR, context=None):
    state = _load(db)
    if state.get("horizon_ms", HOUR) != horizon:
        raise ValueError("Saved model has a different target horizon")
    current = {o.symbol: o for o in observations
               if 0 <= at - o.received_at <= 60_000 and 0 < o.bid <= o.ask
               and o.fast > 0 and o.slow > 0 and 0 < at - o.bar_time <= HOUR + 60_000}
    # Expired targets never become late, backfilled training examples.
    db.execute("UPDATE opportunities SET status='expired' WHERE status='pending' AND expires<?", (at,))
    pending = db.execute("SELECT id,symbol,body FROM opportunities WHERE status='pending' AND due<=? ORDER BY due,id", (at,)).fetchall()
    for row_id, symbol, body in pending:
        if symbol not in current:
            continue
        example = json.loads(body)
        if example["version"] != VERSION:
            raise ValueError("Pending example has a different feature version")
        quote = current[symbol]
        net = (quote.bid * (1 - dec(example["slippage"])) * (1 - dec(example["fee"])) /
               (dec(example["ask"]) * (1 + dec(example["slippage"])) * (1 + dec(example["fee"])))) - 1
        outcome = int(net > 0)
        if not math.isfinite(float(net)):
            raise ValueError("Opportunity outcome exceeds numeric limits")
        example.update(outcome=outcome, net_return=str(net), outcome_at=at, exit_bid=str(quote.bid),
                       outcome_quote_received_at=quote.received_at)
        _score(db, state, example, outcome, float(net), at)
        # Stored probabilities are scored before any weights see their outcome.
        state["candidate"] = train(state["candidate"], example["features"], outcome, state["labels"])
        state["labels"] += 1
        state["positives"] += outcome
        db.execute("UPDATE opportunities SET status='resolved', body=? WHERE id=?", (_json(example), row_id))
    _checkpoint(db, state, at)
    actions = {d["symbol"]: d for d in decisions}
    for observation in observations:
        if observation.symbol not in current or observation.bar_time >= at:
            continue
        if horizon > HOUR:
            previous = db.execute("SELECT max(at) FROM opportunities WHERE symbol=?", (observation.symbol,)).fetchone()[0]
            if previous is not None and at - previous < horizon:
                continue  # no overlapping target windows for swing specialists
        if db.execute("SELECT 1 FROM opportunities WHERE symbol=? AND bar_time=?",
                      (observation.symbol, observation.bar_time)).fetchone():
            continue
        values = features(observation, at)
        market = observation.market or {}
        condition = ("volatile" if market.get("volatile") else "rising" if observation.fast > observation.slow * dec("1.002")
                     else "falling" if observation.fast < observation.slow * dec("0.998") else "sideways")
        decision = actions.get(observation.symbol, {"action": "WAIT", "reason": "No actual decision recorded."})
        trial = state["trial"]
        example = {"version": VERSION, "features": values, "feature_names": FEATURES,
                   "symbol": observation.symbol, "bar_time": observation.bar_time, "predicted_at": at,
                   "due": at + horizon, "expires": at + horizon + GRACE, "bid": str(observation.bid), "ask": str(observation.ask),
                   "fast": str(observation.fast), "slow": str(observation.slow), "horizon_ms": horizon, "context": context,
                   "quote_received_at": observation.received_at, "fee": str(fee), "slippage": str(slippage),
                   "action": decision["action"], "reason": decision["reason"], "condition": condition,
                   "evidence": market, "model_id": state["champion"]["id"],
                   "probability": probability(state["champion"]["weights"], values),
                   "candidate_probability": probability(state["candidate"], values),
                   "baseline_probability": (state["positives"] + 1) / (state["labels"] + 2),
                   "fallback_probability": probability(state["fallback"]["weights"], values) if state["fallback"] else None,
                   "trial_id": trial["id"] if trial else None,
                   "trial_probability": probability(trial["weights"], values) if trial else None,
                   "training_labels": state["labels"], "role": "advisory_only"}
        db.execute("INSERT INTO opportunities(symbol,bar_time,at,due,expires,status,body) VALUES (?,?,?,?,?,'pending',?)",
                   (observation.symbol, observation.bar_time, at, at + horizon, at + horizon + GRACE, _json(example)))
    db.execute("UPDATE prediction_state SET body=? WHERE id=1", (_json(state),))


def _score(db, state, example, label, net, at):
    model_loss = (example["probability"] - label) ** 2
    baseline_loss = (example["baseline_probability"] - label) ** 2
    for score in (state["score"], state["regimes"].setdefault(example["condition"], {"n": 0, "model": 0.0, "baseline": 0.0})):
        score["n"] += 1
        score["model"] += model_loss
        score["baseline"] += baseline_loss
    trial = state["trial"]
    if trial and example["trial_id"] == trial["id"]:
        trial["n"] += 1
        trial["model"] += (example["trial_probability"] - label) ** 2
        trial["champion"] += model_loss
        trial["baseline"] += baseline_loss
        if example["trial_probability"] >= 0.6:
            trial["signals"] += 1
            trial["net"] += net
    if state["champion"]["id"] != "baseline-0" and example["model_id"] == state["champion"]["id"]:
        fallback_loss = ((example["fallback_probability"] - label) ** 2
                         if example["fallback_probability"] is not None else (0.5 - label) ** 2)
        state["drift"].append([model_loss, baseline_loss, fallback_loss])
        if len(state["drift"]) == DRIFT_BLOCK:
            losses = [sum(row[i] for row in state["drift"]) / DRIFT_BLOCK for i in range(3)]
            state["bad_blocks"] = state["bad_blocks"] + 1 if losses[0] > losses[1] + 0.03 else 0
            state["drift"] = []
            if state["bad_blocks"] >= 2:
                previous = state["champion"]["id"]
                state["champion"] = state["fallback"] if state["fallback"] and losses[2] < losses[0] else {
                    "id": "baseline-0", "weights": [0.0] * len(FEATURES)}
                state.update(fallback=None, trial=None, drift=[], bad_blocks=0,
                             candidate=list(state["champion"]["weights"]),
                             next_freeze=state["labels"] + 1 + TRAIN_SAMPLES, rollbacks=state["rollbacks"] + 1)
                state["message"] = "Prediction model rolled back after two weak scoring blocks; trading rules are unchanged."
                _event(db, at, "ROLLBACK", {"previous": previous, "selected": state["champion"]["id"], "losses": losses})


def _checkpoint(db, state, at):
    trial = state["trial"]
    if trial is not None and trial["n"] >= VALIDATION_SAMPLES and at - trial["started_at"] >= VALIDATION_TIME:
        n = trial["n"]
        passed = (trial["model"] / n + 0.01 < trial["champion"] / n
                  and trial["model"] / n + 0.005 < trial["baseline"] / n
                  and trial["signals"] >= 10 and trial["net"] > 0)
        _event(db, at, "VALIDATION", {**trial, "passed": passed})
        if passed:
            state["fallback"] = state["champion"]
            state["champion"] = {"id": trial["id"], "weights": list(trial["weights"])}
            state.update(drift=[], bad_blocks=0, promotions=state["promotions"] + 1)
        state.update(trial=None, next_freeze=state["labels"] + TRAIN_SAMPLES,
                     message="Forecast validation passed; advisory model updated." if passed else
                     "Forecast validation did not establish improvement; retained the advisory model.")
    if state["trial"] is None and state["labels"] >= state["next_freeze"]:
        model_id = f"model-{state['next_version']}"
        state["next_version"] += 1
        trial = {"id": model_id, "weights": list(state["candidate"]), "started_at": at,
                 "training_labels": state["labels"], "n": 0, "model": 0.0, "champion": 0.0,
                 "baseline": 0.0, "signals": 0, "net": 0.0}
        checkpoint = {"version": VERSION, "features": FEATURES, "horizon_ms": state.get("horizon_ms", HOUR), **trial}
        checkpoint["sha256"] = hashlib.sha256(_json(checkpoint).encode()).hexdigest()
        db.execute("INSERT INTO prediction_models VALUES (?,?,?)", (model_id, at, _json(checkpoint)))
        state["trial"] = trial
        state["message"] = "Frozen challenger collecting separate future validation outcomes; trading rules are unchanged."
        _event(db, at, "CHECKPOINT", {"id": model_id, "training_labels": state["labels"]})


def _event(db, at, kind, body):
    db.execute("INSERT INTO prediction_events(at,kind,body) VALUES (?,?,?)", (at, kind, _json(body)))


def summary(db):
    try:
        state = _load(db)
        counts = dict(db.execute("SELECT status,count(*) FROM opportunities GROUP BY status"))
        state["counts"] = counts
        state["recent"] = [{"status": status, **json.loads(body)} for status, body in db.execute(
            "SELECT status,body FROM opportunities ORDER BY id DESC LIMIT 12")]
        return state
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        return {"error": f"Prediction view unavailable: {str(exc)[:160]}"}
