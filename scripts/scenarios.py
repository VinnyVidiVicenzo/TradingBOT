"""Analyst step: good, middle, bad and disaster cases for $200.

Scenarios are taken straight from history rather than invented. For each asset,
every possible 1-year (and 3-year) holding period since its record begins is
measured, then:

  good      a result only about 1 period in 5 beat   (80th percentile)
  middle    the typical result                        (median)
  bad       about 1 period in 5 was this bad or worse (20th percentile)
  disaster  about 1 period in 20 was this bad or worse (5th percentile)

So the "likelihoods" are frequencies from the past, not forecasts. The future
can be better or worse than anything in the record -- including a coin going to
zero, which no surviving coin's history can show.

Sources: Yahoo Finance daily closes (BTC, ETH, XRP, SOL, ZEC, DOGE, BNB in USD;
SPY dividend-adjusted; 3-month T-bill), cached in data/reference/prices/.
Yahoo's Bitcoin matched our Binance.US prices to 0.06% on a typical day.
Trading costs are not deducted here; at $200 they are around 1% round trip.

    python scripts/scenarios.py
"""

from __future__ import annotations

import csv
import json
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CACHE = Path(__file__).parent.parent / "data/reference/prices"
REF = Path(__file__).parent.parent / "data/reference"
ASSETS = {"Bitcoin": "BTC-USD", "Ethereum": "ETH-USD", "XRP": "XRP-USD", "Solana": "SOL-USD",
          "Zcash": "ZEC-USD", "Dogecoin": "DOGE-USD", "BNB": "BNB-USD"}
STAKE = 200.0


def yahoo(symbol: str) -> dict[date, float]:
    path = CACHE / f"{symbol}.csv"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
               "?period1=946684800&period2=1790640000&interval=1d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())["chart"]["result"][0]
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "value"])
            for t, v in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
                if v is not None:
                    w.writerow([datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(), v])
        time.sleep(1)
    return {date.fromisoformat(r["date"]): float(r["value"]) for r in csv.DictReader(path.open())}


def ref(name: str) -> dict[date, float]:
    return {date.fromisoformat(r["date"]): float(r["value"])
            for r in csv.DictReader((REF / name).open())}


def outcomes(prices: dict[date, float], years: int) -> list[float]:
    """$200 after `years`, from every calendar day with a price."""
    days = sorted(prices)
    out, j = [], 0
    for d in days:
        target = d + timedelta(days=365 * years)
        while j < len(days) and days[j] < target:
            j += 1
        if j >= len(days):
            break
        out.append(STAKE * prices[days[j]] / prices[d])
    return out


def savings(tbill: dict[date, float], years: int) -> list[float]:
    days = sorted(tbill)
    out = []
    for i, d in enumerate(days):
        end = d + timedelta(days=365 * years)
        if end > days[-1]:
            break
        v, k = STAKE, i
        while k + 1 < len(days) and days[k + 1] <= end:
            gap = (days[k + 1] - days[k]).days
            v *= 1 + tbill[days[k]] / 100 * gap / 365
            k += 1
        out.append(v)
    return out[::5]   # thinned: savings barely vary day to day


def pct(sorted_vals: list[float], p: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(p * len(sorted_vals)))]


def main() -> None:
    series = {name: yahoo(sym) for name, sym in ASSETS.items()}
    series["Stock fund (SPY)"] = ref("spy_long.csv")
    tbill = ref("tbill_long.csv")

    for years in (1, 3):
        print(f"\n=== $200 HELD {years} YEAR{'S' if years > 1 else ''} -- scenarios taken from history ===")
        print(f"{'asset':<18}{'record from':>12}{'disaster':>10}{'bad':>9}{'middle':>9}{'good':>9}"
              f"{'lost money':>12}{'lost 3/4+':>11}")
        print(f"{'':<18}{'':>12}{'(1 in 20)':>10}{'(1 in 5)':>9}{'':>9}{'(1 in 5)':>9}")
        print("-" * 90)
        rows = [("Savings account", savings(tbill, years), min(tbill))]
        rows += [(n, outcomes(s, years), min(s)) for n, s in series.items()]
        for name, vals, start in rows:
            v = sorted(vals)
            lost = sum(1 for x in v if x < STAKE) / len(v)
            wiped = sum(1 for x in v if x < STAKE / 4) / len(v)
            print(f"{name:<18}{start.isoformat()[:7]:>12}{pct(v, .05):>10,.0f}{pct(v, .20):>9,.0f}"
                  f"{pct(v, .50):>9,.0f}{pct(v, .80):>9,.0f}{lost * 100:>11.0f}%{wiped * 100:>10.0f}%")
    print("\nlost 3/4+ = ended below $50.")


if __name__ == "__main__":
    main()
