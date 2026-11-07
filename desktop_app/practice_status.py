"""Explain saved practice observations without changing trade decisions."""

from __future__ import annotations

from decimal import Decimal

from crypto_research.decimals import dec
from crypto_research.timeutil import HOUR_MS, MINUTE_MS, to_iso
from paper_trading.learning import entry_margin
from paper_trading.practice import BUDGET, SYMBOLS


def duration(milliseconds: int) -> str:
    seconds = max(0, (int(milliseconds) + 999) // 1000)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def utc_time(at: int) -> str:
    return to_iso(at)[11:19] + " UTC"


def trend(entry: dict):
    if "fast" not in entry or "slow" not in entry:
        return None
    fast, slow = dec(entry["fast"]), dec(entry["slow"])
    return (fast / slow - 1) * 100 if slow > 0 else None


def entry_rows(state: dict, at: int) -> list[dict]:
    """Use latest saved averages, not a prediction of when a buy will happen."""
    latest = {}
    for entry in state.get("activity", ()):
        symbol = entry.get("symbol")
        if symbol in SYMBOLS and symbol not in latest and trend(entry) is not None:
            latest[symbol] = entry
    margin = entry_margin(state) * 100
    qualifying = sum(symbol not in state.get("positions", {}) and trend(entry) > margin
                     for symbol, entry in latest.items())
    cash_share = dec(state["cash"]) / max(1, qualifying)
    rows = []
    for symbol in SYMBOLS:
        entry = latest.get(symbol)
        gap = trend(entry) if entry else None
        next_close = int(entry["bar_time"]) + HOUR_MS + 1 if entry and "bar_time" in entry else None
        active = state.get("active", False)
        stale = bool(entry and at - entry["at"] > 2 * MINUTE_MS)
        if not active:
            blocker = "Paused — resume to check prices"
        elif not entry:
            blocker = "Waiting for the first price check"
        elif stale:
            blocker = "Saved prices are old — needs a refresh"
        elif symbol in state.get("positions", {}):
            blocker = "Holding — watching exit conditions"
        elif dec(state.get("equity", state["cash"])) <= BUDGET * Decimal("0.8"):
            # This is only explanatory: the existing engine enforces its loss limit.
            blocker = "Wallet loss threshold reached — check session status"
        elif dec(state["cash"]) < Decimal("10"):
            blocker = "Not enough available practice cash"
        elif gap <= margin:
            blocker = "Needs a stronger upward trend"
        elif cash_share < Decimal("10"):
            blocker = "Available cash share is below the $10 practice minimum"
        else:
            blocker = "Entry trend met — next hourly evaluation pending"
        if not active:
            next_text = "Paused"
        elif next_close is None:
            next_text = "After first price check"
        elif at >= next_close:
            next_text = "Due — awaiting new hourly data"
        else:
            next_text = f"{utc_time(next_close)} ({duration(next_close - at)})"
        rows.append({"symbol": symbol, "blocker": blocker,
                     "gap": f"{gap:+.3f}%" if gap is not None else "Unknown",
                     "target": f"> +{margin:.3f}%", "next": next_text,
                     "observed_at": entry["at"] if entry else None})
    return rows


def refresh_status(state: dict, at: int, *, busy: bool = False) -> str:
    if busy:
        return "Refreshing public prices… Entry checks use completed hours; exit checks run each price refresh."
    if not state.get("active"):
        return "Paused: no new decisions. Learning hours do not block practice entries."
    last = state.get("last_check", 0)
    if not last:
        return "Waiting for the first price check. Learning hours do not block practice entries."
    age = at - last
    if age < 0:
        return "Saved check is ahead of the current clock — check clock settings."
    if age > 2 * MINUTE_MS:
        return f"Last prices {duration(age)} ago — waiting for a successful refresh. Learning hours do not block entries."
    next_poll = max(0, last + MINUTE_MS - at)
    return (f"Prices checked {duration(age)} ago | Next price refresh: "
            + (f"about {duration(next_poll)}" if next_poll else "due shortly")
            + " | Learning hours do not block entries.")


def reason(entry: dict) -> str:
    original = entry["reason"]
    if original != "Waiting for a new completed hour.":
        return original
    gap = trend(entry)
    if gap is None:
        return original
    target = dec(entry.get("entry_margin", "0.002")) * 100
    prefix = "Trend below entry requirement" if gap <= target else "Hour already evaluated"
    next_time = utc_time(int(entry["bar_time"]) + HOUR_MS + 1) if "bar_time" in entry else "next hourly close"
    return f"{prefix}: {gap:+.3f}% vs > +{target:.3f}%; recheck after {next_time}."
