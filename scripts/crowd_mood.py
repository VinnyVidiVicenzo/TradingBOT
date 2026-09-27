"""Analyst step: crowd psychology -- fear, greed, attention, and real money moving.

Four free, public sources, none needing an account:

  1. Fear & Greed Index (alternative.me) -- a daily 0-100 crowd-mood score built
     from volatility, momentum, social volume, dominance and search trends.
     Tested against what Bitcoin's OWN price actually did next, so the question
     is answered with data rather than the score's reputation.
  2. Wikipedia page views for the "Bitcoin" article -- a direct, unmanipulated
     count of public attention. Spikes have historically lined up with mania.
  3. DefiLlama's Solana figure -- not the usage data we wanted (still no free
     source for Solana active addresses or transactions), but it partly fills
     the gap: total value locked in Solana's applications, a sign of real money
     using the network rather than just holding the coin.
  4. Bitcoin ETF money flows (Farside Investors) -- actual investor money
     moving in and out each day. Read once through a browser (the site blocks
     plain fetches); the last two weeks plus lifetime totals, hand-recorded in
     results/etf_flows_farside.md because it is a one-off snapshot, not an API.

What was tried and does not have a free, reliable source: Google Trends (no
official API; the unofficial route is frequently blocked) and live betting-market
odds specific to crypto regulation (nothing matching was open on Polymarket at
the time of writing). Congressional trading disclosures in crypto-adjacent
stocks (Coinbase, Strategy, Bitcoin ETFs) would be a genuinely interesting
"obscure" source -- whether lawmakers voting on crypto rules also trade
crypto-linked stocks -- but the connected data plan does not include it.

Honest limits: a crowd-mood score is a description of recent sentiment, not a
cause of future price moves, and it is compiled from the same market data that
produces the price, so some of the "prediction" is circular by construction.
Wikipedia views can spike for reasons unrelated to price (a hack, a documentary,
a it up in the news for a legal case). Treat all of this as colour, not signal.

    python scripts/crowd_mood.py
"""

from __future__ import annotations

import csv
import json
import statistics
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REF = Path(__file__).parent.parent / "data/reference"
RESULTS = Path(__file__).parent.parent / "results"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fetch_fear_greed() -> dict[date, tuple[int, str]]:
    cache = REF / "fear_greed.csv"
    if not cache.exists():
        data = json.loads(get("https://api.alternative.me/fng/?limit=0&format=json"))["data"]
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "value", "classification"])
            for row in data:
                d = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc).date()
                w.writerow([d.isoformat(), row["value"], row["value_classification"]])
    out = {}
    for r in csv.DictReader(cache.open(encoding="utf-8")):
        out[date.fromisoformat(r["date"])] = (int(r["value"]), r["classification"])
    return out


def fetch_wikipedia_views() -> dict[date, int]:
    cache = REF / "wikipedia_pageviews_btc.csv"
    if not cache.exists():
        start, end = date(2015, 7, 1), date(2026, 9, 26)
        url = (
            "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
            f"en.wikipedia/all-access/all-agents/Bitcoin/daily/"
            f"{start:%Y%m%d}/{end:%Y%m%d}"
        )
        items = json.loads(get(url))["items"]
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "views"])
            for item in items:
                d = datetime.strptime(item["timestamp"][:8], "%Y%m%d").date()
                w.writerow([d.isoformat(), item["views"]])
    return {date.fromisoformat(r["date"]): int(r["views"])
            for r in csv.DictReader(cache.open(encoding="utf-8"))}


def fetch_solana_tvl() -> dict[date, float]:
    cache = REF / "defillama_solana_tvl.csv"
    if not cache.exists():
        rows = json.loads(get("https://api.llama.fi/v2/historicalChainTvl/Solana"))
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "tvl_usd"])
            for r in rows:
                d = datetime.fromtimestamp(r["date"], tz=timezone.utc).date()
                w.writerow([d.isoformat(), r["tvl"]])
    return {date.fromisoformat(r["date"]): float(r["tvl_usd"])
            for r in csv.DictReader(cache.open(encoding="utf-8"))}


def btc_prices() -> dict[date, float]:
    """Reuse the long Bitcoin price history already cached for this project."""
    path = REF / "btc_long.csv"
    return {date.fromisoformat(r["date"]): float(r["value"])
            for r in csv.DictReader(path.open(encoding="utf-8"))}


def forward_return(prices: dict[date, float], start: date, days: int) -> float | None:
    all_days = sorted(prices)
    if start not in prices:
        return None
    target = start + timedelta(days=days)
    later = [d for d in all_days if d >= target]
    if not later:
        return None
    return prices[later[0]] / prices[start] - 1


def main() -> None:
    fg = fetch_fear_greed()
    views = fetch_wikipedia_views()
    sol_tvl = fetch_solana_tvl()
    btc = btc_prices()

    # -- 1. Fear & Greed vs what actually happened next -----------------
    print("1. FEAR & GREED INDEX vs BITCOIN'S OWN NEXT RETURNS\n")
    dates = sorted(fg)
    print(f"  history: {dates[0]} to {dates[-1]} ({len(dates)} days)")
    today_val, today_label = fg[dates[-1]]
    below = sum(1 for d in dates if fg[d][0] < today_val) / len(dates)
    print(f"  today's reading: {today_val} ({today_label}) -- higher than "
          f"{below*100:.0f}% of all days on record\n")

    buckets = {
        "extreme fear (0-24)": [d for d in dates if fg[d][0] <= 24],
        "fear (25-44)": [d for d in dates if 25 <= fg[d][0] <= 44],
        "neutral (45-55)": [d for d in dates if 45 <= fg[d][0] <= 55],
        "greed (56-75)": [d for d in dates if 56 <= fg[d][0] <= 75],
        "extreme greed (76-100)": [d for d in dates if fg[d][0] >= 76],
    }
    print(f"  {'reading that day':<24}{'days':>6}{'next 30d avg':>14}{'next 30d fell':>15}"
          f"{'next 90d avg':>14}{'next 90d fell':>15}")
    for label, days_in_bucket in buckets.items():
        r30 = [forward_return(btc, d, 30) for d in days_in_bucket]
        r30 = [x for x in r30 if x is not None]
        r90 = [forward_return(btc, d, 90) for d in days_in_bucket]
        r90 = [x for x in r90 if x is not None]
        if r30:
            fell30 = f"{sum(1 for x in r30 if x < 0)}/{len(r30)}"
            fell90 = f"{sum(1 for x in r90 if x < 0)}/{len(r90)}" if r90 else "n/a"
            avg90 = f"{statistics.mean(r90)*100:+.1f}%" if r90 else "n/a"
            print(f"  {label:<24}{len(days_in_bucket):>6}{statistics.mean(r30)*100:>+13.1f}%"
                  f"{fell30:>15}{avg90:>14}{fell90:>15}")

    # -- 2. Wikipedia attention vs price ----------------------------------
    print("\n\n2. PUBLIC ATTENTION (Wikipedia 'Bitcoin' page views/day)\n")
    vdates = sorted(views)
    print(f"  history: {vdates[0]} to {vdates[-1]}")
    recent = statistics.mean(views[d] for d in vdates[-30:])
    peak_day = max(vdates, key=lambda d: views[d])
    print(f"  last 30 days average: {recent:,.0f} views/day")
    print(f"  all-time peak: {views[peak_day]:,} views on {peak_day} "
          f"(price that day: ${btc.get(peak_day, 0):,.0f})" if peak_day in btc else "")
    top10 = sorted(vdates, key=lambda d: -views[d])[:10]
    print("  top 10 attention spikes on record:")
    for d in sorted(top10):
        price_note = f"BTC ${btc[d]:,.0f}" if d in btc else "BTC price n/a"
        print(f"    {d}: {views[d]:>7,} views  ({price_note})")

    # -- 3. Solana TVL (partial usage fill) -------------------------------
    print("\n\n3. SOLANA -- MONEY ACTUALLY USING THE NETWORK (DefiLlama TVL)\n")
    sdates = sorted(sol_tvl)
    now_tvl = sol_tvl[sdates[-1]]
    def near(target: date) -> float | None:
        cands = [d for d in sdates if d <= target]
        return sol_tvl[cands[-1]] if cands else None
    y1 = near(sdates[-1] - timedelta(days=365))
    y3 = near(sdates[-1] - timedelta(days=3*365))
    print(f"  today: ${now_tvl/1e9:.2f}bn locked in Solana applications")
    if y1:
        print(f"  vs 1 year ago: {(now_tvl/y1-1)*100:+.0f}%")
    if y3:
        print(f"  vs 3 years ago: {(now_tvl/y3-1)*100:+.0f}%")
    print("  (this is money deposited in Solana apps, not active addresses or")
    print("   transactions -- the free-data gap for Solana usage remains open)")

    print("\n\nSee results/etf_flows_farside.md for Bitcoin ETF money flows around")
    print("the CLARITY Act vote, read directly from the source site.")


if __name__ == "__main__":
    main()
