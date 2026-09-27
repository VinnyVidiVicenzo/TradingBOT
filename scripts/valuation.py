"""Analyst step: is it expensive right now?

Uses the "market value to realised value" ratio (MVRV) from Coin Metrics'
free community data. Realised value prices every coin at the moment it last
moved, which approximates what holders collectively paid. So:

  1.0  today's price equals what holders paid, on average
  >1   holders sit on average profit (3.0 = triple what they paid)
  <1   holders sit on average loss

Three questions:
  1. Where is each coin today, against its own history?
  2. What did it read at past peaks and crash bottoms?
  3. The test that matters: has a high or low reading actually told you anything
     about the NEXT twelve months? (For Bitcoin, where history is longest.)

Honest limits:
  * Bitcoin has only about four full boom-bust cycles. Four examples are far too
    few to trust a rule, and patterns that fit the past are easy to find after
    the fact. Treat this as "stretched or not", not as a timing signal.
  * Since 2024 much Bitcoin sits in stock-market funds and rarely moves, which
    can change how the ratio behaves compared with earlier cycles.
  * Solana and BNB are not covered by the free data.

    python scripts/valuation.py
"""

from __future__ import annotations

import csv
import json
import statistics
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).parent.parent / "data/reference/fundamentals"
ASSETS = {"BTC": "btc", "ETH": "eth", "XRP": "xrp", "ZEC": "zec", "DOGE": "doge"}
TODAY = date(2026, 9, 26)

MOMENTS = [  # well-known turning points, used only to label the history
    ("2017-12-17", "peak Dec 2017"), ("2018-12-15", "bottom Dec 2018"),
    ("2021-04-14", "peak Apr 2021"), ("2021-11-10", "peak Nov 2021"),
    ("2022-11-21", "bottom Nov 2022"), ("2024-03-14", "peak Mar 2024"),
    ("2025-10-06", "peak Oct 2025"), ("2026-06-06", "low Jun 2026"),
]


def fetch(asset: str) -> dict[date, tuple[float, float]]:
    cache = OUT / f"coinmetrics_mvrv_{asset}.csv"
    if not cache.exists():
        rows, url = [], ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
                         f"?assets={asset}&metrics=CapMVRVCur,PriceUSD&frequency=1d"
                         "&start_time=2013-01-01&page_size=10000")
        while url:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
            page = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())
            rows.extend(page["data"])
            url = page.get("next_page_url")
            time.sleep(1)
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "mvrv", "price"])
            for r in rows:
                if r.get("CapMVRVCur") and r.get("PriceUSD"):
                    w.writerow([r["time"][:10], r["CapMVRVCur"], r["PriceUSD"]])
    return {date.fromisoformat(r["date"]): (float(r["mvrv"]), float(r["price"]))
            for r in csv.DictReader(cache.open(encoding="utf-8"))}


def near(series: dict[date, tuple[float, float]], when: date) -> tuple[float, float] | None:
    for k in range(0, 8):
        for d in (when - timedelta(days=k), when + timedelta(days=k)):
            if d in series:
                return series[d]
    return None


def main() -> None:
    data = {sym: fetch(a) for sym, a in ASSETS.items()}

    print("1. WHERE EACH COIN SITS TODAY\n")
    print(f"{'coin':<6}{'today':>8}{'history from':>14}{'lowest ever':>13}{'highest ever':>14}"
          f"{'higher than today on':>23}")
    for sym, s in data.items():
        dates = sorted(s)
        now = s[dates[-1]][0]
        vals = [s[d][0] for d in dates]
        share_above = sum(1 for v in vals if v > now) / len(vals)
        print(f"{sym:<6}{now:>8.2f}{dates[0].isoformat():>14}{min(vals):>13.2f}{max(vals):>14.2f}"
              f"{share_above * 100:>17.0f}% of days")

    print("\n\n2. WHAT IT READ AT PAST TURNING POINTS\n")
    print(f"{'moment':<20}" + "".join(f"{s:>8}" for s in data))
    for iso, label in MOMENTS:
        cells = []
        for s in data.values():
            v = near(s, date.fromisoformat(iso))
            cells.append("  -" if v is None else f"{v[0]:.2f}")
        print(f"{label:<20}" + "".join(f"{c:>8}" for c in cells))
    print(f"{'TODAY':<20}" + "".join(f"{s[max(s)][0]:>8.2f}" for s in data.values()))

    print("\n\n3. DID IT PREDICT THE NEXT 12 MONTHS?  (Bitcoin, every month since 2012)\n")
    btc = data["BTC"]
    months = sorted({d.replace(day=1) for d in btc})
    buckets: dict[str, list[float]] = {"below 1.0": [], "1.0-1.5": [], "1.5-2.5": [],
                                       "2.5-3.5": [], "above 3.5": []}
    for m in months:
        start = near(btc, m)
        end = near(btc, m + timedelta(days=365))
        if not start or not end or m + timedelta(days=365) > TODAY:
            continue
        ratio, price = start
        ret = end[1] / price - 1
        key = ("below 1.0" if ratio < 1 else "1.0-1.5" if ratio < 1.5 else
               "1.5-2.5" if ratio < 2.5 else "2.5-3.5" if ratio < 3.5 else "above 3.5")
        buckets[key].append(ret)
    print(f"{'reading at the start':<22}{'months':>8}{'typical next 12m':>18}{'fell over next 12m':>21}")
    for key, rets in buckets.items():
        if rets:
            print(f"{key:<22}{len(rets):>8}{statistics.median(rets) * 100:>+17.0f}%"
                  f"{sum(1 for r in rets if r < 0):>13} of {len(rets)}")
    now = btc[max(btc)][0]
    print(f"\n  Bitcoin today reads {now:.2f}.")
    print("  Months in the same bucket overlap heavily and come from only ~4 cycles,")
    print("  so these are a handful of independent episodes, not dozens.")

    out = Path(__file__).parent.parent / "results/valuation.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["coin", "today_mvrv", "share_of_days_higher"])
        for sym, s in data.items():
            vals = [v[0] for v in s.values()]
            now = s[max(s)][0]
            w.writerow([sym, now, sum(1 for v in vals if v > now) / len(vals)])


if __name__ == "__main__":
    main()
