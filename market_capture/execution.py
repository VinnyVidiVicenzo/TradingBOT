"""Causal top-20 depth replay. No inference of passive order fills or queue rank."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from crypto_research.decimals import dec
from crypto_research.market_universe import SYMBOLS
from .service import levels


def roundtrip(entry, exit_book, notional, fee, slippage):
    notional, fee, slippage = dec(notional), dec(fee), dec(slippage)
    if notional <= 0 or not 0 <= fee <= dec("0.1") or not 0 <= slippage <= dec("0.1"):
        raise ValueError("Invalid execution scenario")
    asks = levels(entry["asks"])
    bids = levels(exit_book["bids"])
    remaining, quantity = notional / (1 + fee), dec(0)
    for price, size in asks:
        price *= 1 + slippage
        take = min(size, remaining / price)
        remaining -= take * price
        quantity += take
        if remaining <= dec("0.000000000001"):
            break
    if remaining > dec("0.000000000001"):
        return {"status": "insufficient_entry_depth", "unfilled_quote": str(remaining), "return_pct": None}
    unsold, proceeds = quantity, dec(0)
    for price, size in bids:
        take = min(size, unsold)
        proceeds += take * price * (1 - slippage) * (1 - fee)
        unsold -= take
        if unsold <= dec("0.000000000001"):
            break
    if unsold > dec("0.000000000001"):
        return {"status": "insufficient_exit_depth", "unsold_quantity": str(unsold), "return_pct": None}
    return {"status": "estimated_full_fill", "return_pct": str((proceeds / notional - 1) * 100),
            "proceeds": str(proceeds), "quantity": str(quantity),
            "limitation": "Aggressive hypothetical fills only; partial depth, latency and visible liquidity are not fill guarantees."}


def replay(path, symbol, notional, *, hold_ms=60_000, latency_ms=100, fee="0.004", slippage="0.001"):
    if symbol not in SYMBOLS or hold_ms < 1000 or not 0 <= latency_ms <= 60_000:
        raise ValueError("Invalid replay scenario")
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        decision = db.execute("SELECT received,session FROM stream_events WHERE symbol=? AND kind='depth' ORDER BY id LIMIT 1", (symbol,)).fetchone()
        if decision is None:
            return {"status": "no_depth_data"}
        at, session = decision
        def later(target):
            return db.execute("SELECT received,body FROM stream_events WHERE symbol=? AND kind='depth' AND session=? AND received>=? ORDER BY received,id LIMIT 1",
                              (symbol, session, target)).fetchone()
        entry, exit_row = later(at + latency_ms), later(at + latency_ms + hold_ms)
        if entry is None or exit_row is None:
            return {"status": "not_enough_future_depth"}
        if entry[0] - at - latency_ms > 1000 or exit_row[0] - at - latency_ms - hold_ms > 1000:
            return {"status": "stale_depth_window"}
        gap = db.execute("SELECT 1 FROM stream_events WHERE kind='GAP' AND received>=? AND received<=? LIMIT 1", (at, exit_row[0])).fetchone()
        if gap:
            return {"status": "capture_gap_withheld"}
        first, last = json.loads(entry[1])["data"], json.loads(exit_row[1])["data"]
        result = roundtrip(first, last, notional, fee, slippage)
        result.update(decision_at=at, entry_at=entry[0], exit_at=exit_row[0], latency_ms=latency_ms,
                      stress=roundtrip(first, last, notional, min(dec("0.1"), 2 * dec(fee)),
                                       min(dec("0.1"), 2 * dec(slippage))),
                      passive_fills="Not modeled: resting orders can remain unfilled; queue rank is unknown.")
        return result
    finally:
        db.close()
