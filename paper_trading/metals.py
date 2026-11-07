"""Manual physical-inventory scenarios. No market orders or hedge recommendations."""
from __future__ import annotations

import json
from pathlib import Path

from crypto_research.decimals import dec
from crypto_research.timeutil import parse_time


def lot(payload):
    if payload["metal"] not in ("silver", "gold"):
        raise ValueError("Choose silver or gold")
    if type(payload.get("sold", False)) is not bool:
        raise ValueError("Invalid sold flag")
    result = {"metal": payload["metal"], "description": str(payload["description"])[:120],
              "quote_date": str(payload["quote_date"]), "sold": bool(payload.get("sold", False))}
    parse_time(result["quote_date"])
    for field in ("ounces", "cost", "bid", "spot", "exit_cost"):
        value = dec(payload[field])
        if value < 0 or (field != "exit_cost" and value == 0):
            raise ValueError(f"{field} must be positive (exit cost may be zero)")
        result[field] = str(value)
    return result


def load(path: Path):
    if not path.exists():
        return []
    if path.stat().st_size > 2_000_000:
        raise ValueError("Inventory file too large")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or len(payload["lots"]) > 5000:
        raise ValueError("Unsupported inventory format")
    return [lot(p) for p in payload["lots"]]


def save(path: Path, lots):
    if len(lots) > 5000:
        raise ValueError("Inventory limit reached")
    validated = [lot(p) for p in lots]
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps({"version": 1, "currency": "USD", "lots": validated}, indent=2), encoding="utf-8")
    pending.replace(path)


def inventory(lots, metal):
    selected = [p for p in lots if p["metal"] == metal and not p.get("sold")]
    ounces = sum((dec(p["ounces"]) for p in selected), dec(0))
    cost = sum((dec(p["cost"]) for p in selected), dec(0))
    exits = sum((dec(p["exit_cost"]) for p in selected), dec(0))
    bid_value = sum((dec(p["ounces"]) * dec(p["bid"]) for p in selected), dec(0)) - exits
    return {"ounces": ounces, "cost": cost, "resale_value": bid_value, "pnl": bid_value - cost,
            "break_even_bid": (cost + exits) / ounces if ounces else None}


def record_sale(lots, index, quantity):
    """Reduce inventory for a completed physical sale, preserving cost basis."""
    result = [dict(value) for value in lots]
    current = result[index]
    quantity, held = dec(quantity), dec(current["ounces"])
    if current["sold"] or not 0 < quantity <= held:
        raise ValueError("Sale quantity must be positive and no greater than the held lot")
    if quantity == held:
        current["sold"] = True
    else:
        portion = dict(current)
        portion.update(sold=True, ounces=str(quantity))
        current["ounces"] = str(held - quantity)
        for field in ("cost", "exit_cost"):
            share = dec(current[field]) * quantity / held
            portion[field] = str(share)
            current[field] = str(dec(current[field]) - share)
        result.append(portion)
    return result


def scenario(lots, metal, spot, shock, *, premium_change="0", hedge=None):
    """Idealized at-expiry spot-linked put; not an actual ETF/futures contract."""
    selected = [p for p in lots if p["metal"] == metal and not p.get("sold")]
    total = inventory(selected, metal)
    spot, shock, premium_change = dec(spot), dec(shock), dec(premium_change)
    if spot <= 0 or shock < -1:
        raise ValueError("Invalid spot/scenario")
    future = spot * (1 + shock)
    resale = sum((max(dec(0), future + dec(p["bid"]) - dec(p["spot"]) + premium_change) * dec(p["ounces"])
                  - dec(p["exit_cost"]) for p in selected), dec(0))
    unhedged = resale - total["cost"]
    payout = premium = dec(0)
    if hedge:
        expiry = hedge["expiry"]
        parse_time(expiry)
        covered, strike, unit_premium = dec(hedge["ounces"]), dec(hedge["strike"]), dec(hedge["premium"])
        if not 0 <= covered <= total["ounces"] or strike <= 0 or unit_premium < 0:
            raise ValueError("Hedge must not exceed held fine ounces; strike/premium must be valid")
        payout = max(dec(0), strike - future) * covered
        premium = unit_premium * covered
    return {"future_spot": future, "unhedged_pnl": unhedged, "hypothetical_hedged_pnl": unhedged + payout - premium,
            "put_payout": payout, "hedge_cost": premium}
