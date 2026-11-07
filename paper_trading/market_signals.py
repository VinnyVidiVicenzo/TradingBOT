"""Sampled exchange activity, not wallet identities or a continuous trade feed.

One five-minute aggregate-trade page and 20 book levels per check. Partial,
stale or malformed samples cannot admit entries in the signal experiment.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from crypto_research.decimals import dec
from crypto_research.timeutil import now_ms

ZERO = Decimal(0)
WINDOW_MS = 300_000
MAX_AGE_MS = 60_000


def collect(client, symbol: str, at: int, *, clock=now_ms) -> dict:
    """An optional feed failure is visible but cannot stop the existing agent."""
    try:
        rows = client.aggregate_trades(symbol, start_time=at - WINDOW_MS, end_time=at, limit=1000)
        book = client.depth(symbol, limit=20)
        summary = summarize(rows, book, at)
        summary["requested_at"] = at
        summary["sampled_at"] = clock()
        return summary
    except Exception as exc:
        return {"status": "unavailable", "sampled_at": at,
                "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}


def summarize(rows, book, at: int) -> dict:
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Invalid trade page")
    total, initiated_buy, largest = ZERO, ZERO, ZERO
    previous_id = previous_time = None
    for row in rows:
        trade_id, when = row["a"], row["T"]
        if (type(trade_id) is not int or type(when) is not int
                or not 0 <= trade_id < 2**63 or not at - WINDOW_MS <= when <= at
                or type(row["m"]) is not bool
                or (previous_id is not None and trade_id != previous_id + 1)
                or (previous_time is not None and when < previous_time)):
            raise ValueError("Invalid or discontinuous trade sample")
        price, quantity = dec(row["p"]), dec(row["q"])
        if price <= 0 or quantity <= 0:
            raise ValueError("Nonpositive trade value")
        notional = dec(price * quantity)
        total = dec(total + notional)
        initiated_buy += notional if not row["m"] else ZERO
        largest = max(largest, notional)
        previous_id, previous_time = trade_id, when
    bids, asks = _levels(book["bids"], descending=True), _levels(book["asks"], descending=False)
    bid, ask = bids[0][0], asks[0][0]
    if bid > ask:
        raise ValueError("Crossed depth book")
    mid = (bid + ask) / 2
    buy_depth = sum((p * q for p, q in asks if p <= mid * Decimal("1.005")), ZERO)
    sell_depth = sum((p * q for p, q in bids if p >= mid * Decimal("0.995")), ZERO)
    status = ("partial" if len(rows) == 1000 else "no_recent_trades" if not rows or
              at - rows[-1]["T"] > MAX_AGE_MS else "ready")
    return {"status": status, "sampled_at": at, "window_start": at - WINDOW_MS,
            "trade_count": len(rows), "first_id": rows[0]["a"] if rows else None,
            "last_id": previous_id, "last_trade_at": previous_time,
            "notional": str(total), "buy_share": str(initiated_buy / total) if total else None,
            "largest_trade": str(largest), "buy_depth": str(dec(buy_depth)),
            "sell_depth": str(dec(sell_depth)), "depth_bid": str(bid), "depth_ask": str(ask),
            "sample_sha256": hashlib.sha256(json.dumps({"trades": rows, "book": book},
                                                       sort_keys=True).encode()).hexdigest()}


def _levels(rows, *, descending):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 20:
        raise ValueError("Invalid depth levels")
    levels = [(dec(row[0]), dec(row[1])) for row in rows]
    if any(p <= 0 or q <= 0 for p, q in levels):
        raise ValueError("Nonpositive depth level")
    if any((a[0] <= b[0] if descending else a[0] >= b[0]) for a, b in zip(levels, levels[1:])):
        raise ValueError("Unordered depth levels")
    return levels


def regime(closed) -> dict:
    """Descriptions are fixed, testable heuristics, not forecasts."""
    fast = sum((dec(b.close) for b in closed[-20:]), ZERO) / 20
    slow = sum((dec(b.close) for b in closed[-50:]), ZERO) / 50
    returns = [dec(b.close) / dec(a.close) - 1 for a, b in zip(closed[-21:-1], closed[-20:])]
    rms = (sum((r * r for r in returns), ZERO) / len(returns)).sqrt()
    volumes = [dec(b.volume) for b in closed[-21:]]
    if any(v < 0 for v in volumes):
        raise ValueError("Negative hourly volume")
    average = sum(volumes[:-1], ZERO) / 20
    return {"trend": "rising" if fast > slow * Decimal("1.002") else
            "falling" if fast < slow * Decimal("0.998") else "sideways",
            "volatile": rms >= Decimal("0.02"), "hourly_rms": str(rms),
            "volume_ratio": str(volumes[-1] / average) if average else None}


def gate(observation, at: int, planned_spend: Decimal) -> tuple[bool, str]:
    signal = observation.market or {}
    if not usable(signal, at):
        return False, "Market sample missing, partial or stale."
    reasons = []
    if signal["trade_count"] < 10:
        reasons.append("Too few recent trades")
    if dec(signal["buy_share"]) < Decimal("0.55"):
        reasons.append("Buy-initiated share below 55%")
    if (observation.ask - observation.bid) / observation.bid > Decimal("0.003"):
        reasons.append("Price spread above 0.3%")
    book_mid = (dec(signal["depth_bid"]) + dec(signal["depth_ask"])) / 2
    if abs(book_mid / ((observation.bid + observation.ask) / 2) - 1) > Decimal("0.005"):
        reasons.append("Depth snapshot differs from the current price")
    if dec(signal["buy_depth"]) < planned_spend * 3:
        reasons.append("Displayed sell offers below three times planned spend")
    if signal.get("volume_ratio") is None or dec(signal["volume_ratio"]) < 1:
        reasons.append("Hourly volume below its 20-hour average or unknown")
    if signal.get("volatile"):
        reasons.append("Unusually volatile hourly prices")
    return not reasons, "; ".join(reasons) if reasons else "Trade activity and liquidity support the trial entry."


def usable(signal, at):
    return (signal.get("status") == "ready"
            and all(0 <= at - signal.get(key, 0) <= MAX_AGE_MS
                    for key in ("sampled_at", "requested_at", "last_trade_at")))


def explanation(observation, at, fee, slippage, spend) -> dict:
    signal = observation.market or {"status": "unavailable"}
    allowed, reason = gate(observation, at, spend)
    roundtrip = 1 - (observation.bid * (1 - slippage) * (1 - fee) /
                     (observation.ask * (1 + slippage) * (1 + fee)))
    return {"market": signal, "signal_entry_supported": allowed, "signal_reason": reason,
            "roundtrip_cost_pct": str(roundtrip * 100), "observed_at": at,
            "quote_received_at": observation.received_at, "signal_role": "comparison_only"}
