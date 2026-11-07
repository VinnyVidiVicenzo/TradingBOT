"""Explicit research fee provenance. Never authenticates or reads account state."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from crypto_research.decimals import dec, format_decimal
from crypto_research.market_universe import SYMBOLS, LEGACY_SYMBOLS


def validate(payload):
    supplied = set(payload["fees"])
    if (payload.get("version") != 1 or not set(LEGACY_SYMBOLS) <= supplied
            or not supplied <= set(SYMBOLS)):
        raise ValueError("Fee profile must contain BTCUSD and ETHUSD; only supported USD markets are accepted")
    assumed = payload.get("assumed_symbols", [])
    if not isinstance(assumed, list) or any(s not in SYMBOLS for s in assumed):
        raise ValueError("Invalid assumed fee symbols")
    assumed = sorted(set(assumed) | (set(SYMBOLS) - supplied))
    fees = {}
    for symbol in SYMBOLS:
        fees[symbol] = {}
        for side in ("maker", "taker"):
            rate = dec(payload["fees"][symbol][side]) if symbol in supplied else dec("0.004")
            if not 0 <= rate <= dec("0.1"):
                raise ValueError("Fee rates must be fractions between 0 and 0.1")
            fees[symbol][side] = format_decimal(rate)
    slip = dec(payload.get("slippage", "0.001"))
    if not 0 <= slip <= dec("0.1"):
        raise ValueError("Slippage must be a fraction between 0 and 0.1")
    observed = payload.get("observed_at")
    if observed is not None and (type(observed) is not int or not 0 <= observed < 2**63):
        raise ValueError("Invalid fee snapshot time")
    result = {"version": 1, "fees": fees, "slippage": format_decimal(slip),
              "source": str(payload.get("source", "user supplied"))[:160], "observed_at": observed,
              "assumed_symbols": assumed,
              "verification": payload.get("verification", "user_supplied"),
              "source_url": str(payload.get("source_url", ""))[:256]}
    if result["verification"] not in ("assumed", "published_schedule", "account_reported", "user_supplied"):
        raise ValueError("Unknown fee provenance")
    economic = json.dumps({"fees": fees, "slippage": format_decimal(slip), "assumed_symbols": assumed}, sort_keys=True)
    result["sha256"] = hashlib.sha256(economic.encode()).hexdigest()
    return result


def load(path: Path):
    if not path.exists():
        return validate({"version": 1, "source": "Assumed fees — not account verified", "observed_at": None,
                         "verification": "assumed",
                         "assumed_symbols": list(SYMBOLS),
                         "fees": {s: {"maker": "0.004", "taker": "0.004"} for s in SYMBOLS}})
    if path.stat().st_size > 65536:
        raise ValueError("Fee profile is too large")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def store(path: Path, payload):
    result = validate(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(result, indent=2), encoding="utf-8")
    pending.replace(path)
    return result


def fee(profile, side="taker"):
    """Conservatively use the highest reported or assumed rate across markets."""
    return max(dec(profile["fees"][s][side]) for s in SYMBOLS)


def published_schedule(at):
    """Current public spot schedule checked 2026-11-07; not an account report."""
    return validate({"version": 1, "source": "Published Binance.US spot schedule — account-specific rates unverified",
                     "verification": "published_schedule", "source_url": "https://www.binance.us/fees",
                     "observed_at": at, "slippage": "0.001",
                     "fees": {s: {"maker": "0", "taker": "0.0002"} for s in SYMBOLS}})
