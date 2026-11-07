"""Independent non-overlapping 4h/24h advisory research, scoped by cost identity."""
from __future__ import annotations

import json
import re

from crypto_research.decimals import dec
from . import prediction, research_costs

HORIZONS = {"4h": 4 * prediction.HOUR, "24h": 24 * prediction.HOUR}
_TABLES = re.compile(r"\b(prediction_state|prediction_models|opportunities|opportunity_pending|prediction_events)\b")


class ScopedDB:
    def __init__(self, db, prefix):
        if not re.fullmatch(r"swing_(4h|24h)_[a-f0-9]{12}", prefix):
            raise ValueError("Invalid swing study identifier")
        self.db, self.prefix = db, prefix

    def execute(self, sql, parameters=()):
        return self.db.execute(_TABLES.sub(lambda m: self.prefix + "_" + m.group(0), sql), parameters)


def scoped(db, name, profile):
    return ScopedDB(db, "swing_" + name + "_" + profile["sha256"][:12])


def advance(db, observations, at, decisions, profile):
    for name, horizon in HORIZONS.items():
        study = scoped(db, name, profile)
        prediction.initialize(study, horizon=horizon)
        prediction.advance(study, observations, at, decisions, fee=research_costs.fee(profile),
                           slippage=dec(profile["slippage"]), horizon=horizon, context={"cost_profile": profile})


def summary(db, profile):
    result = {"cost_profile": profile, "tracks": {}}
    for name in HORIZONS:
        study = scoped(db, name, profile)
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (study.prefix + "_prediction_state",)).fetchone()
        if not exists:
            result["tracks"][name] = {"waiting": True}
            continue
        model = prediction.summary(study)
        if not model.get("error"):
            model["evaluation"] = evaluate(study)
        result["tracks"][name] = model
    return result


def evaluate(study):
    """Normalized hypothetical-opportunity comparisons, not portfolio performance."""
    result = {"n": 0, "cash": 0.0, "model": 0.0, "trend": 0.0, "all": 0.0, "stress": 0.0}
    for (body,) in study.execute("SELECT body FROM opportunities WHERE status='resolved' ORDER BY due,id"):
        row = json.loads(body)
        net = float(dec(row["net_return"]))
        fee, slip = min(dec("0.1"), 2 * dec(row["fee"])), min(dec("0.1"), 2 * dec(row["slippage"]))
        stressed = dec(row["exit_bid"]) * (1 - slip) * (1 - fee) / (dec(row["ask"]) * (1 + slip) * (1 + fee)) - 1
        result["n"] += 1
        result["all"] += net
        result["model"] += net if row["probability"] >= 0.6 else 0
        result["trend"] += net if dec(row["fast"]) > dec(row["slow"]) * dec("1.002") else 0
        result["stress"] += float(stressed) if row["probability"] >= 0.6 else 0
    n = result["n"]
    return {**result, "mean_pct": {k: result[k] * 100 / n if n else None
                                   for k in ("cash", "model", "trend", "all", "stress")}}
