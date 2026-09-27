"""Leverage: does over-betting with borrowed money predict what happens next?

"Leverage" here means trading with borrowed money in the futures market -- a
trader puts down $10 of their own and controls $100 of Bitcoin, for example.
This is NOT something this project's own research tool can do (V1 is spot-only,
no leverage, no futures, no borrowing -- see docs/trading-bot/SAFETY.md). This
script studies leverage in the wider market as an outside indicator, the same
way the crowd-mood study read the Fear & Greed Index: informational only.

Three angles:

  1. Funding rate. On a perpetual futures contract, whichever side (longs or
     shorts) is more crowded pays a fee to the other side every 8 hours. A very
     positive rate means a large, expensive-to-hold crowd of leveraged
     optimists; a very negative rate means the opposite. Extreme readings have
     often preceded sharp reversals, because an overcrowded, expensive trade
     is fragile: a single push against it forces the leveraged side to close,
     which pushes the price further the same way (a "squeeze").
  2. Stablecoin supply growth. New USDT/USDC being created is often new money
     arriving that intends to buy something; large supply contractions can
     mean the opposite. Tested the same way as funding rate and Fear & Greed:
     against Bitcoin's own next-30/90-day return.
  3. A qualitative record of the biggest leverage-driven crashes already
     visible in our own six years of price data (results/risk_profile.csv,
     event_study.py), connecting the DEPTH of those crashes to what is publicly
     documented about forced liquidations during each one.

DATA SOURCE NOTE: Binance.com and Bybit's public futures APIs both refused this
environment's requests as coming from a restricted location. OKX's public API
did not refuse the request, but its funding-rate-history endpoint turned out to
retain only about 3 months (confirmed directly: a query one year back returns
HTTP 200 with an empty result, a real limit rather than a bug) -- too short to
test anything against 90-day forward returns. Deribit's BTC-PERPETUAL, checked
next, has funding history back to mid-2019, so that is the source actually
used below. All of this is read-only, informational market data from other
exchanges, used as an outside indicator exactly the same way this project
already uses CoinGecko, DefiLlama, Etherscan or Yahoo. None of it is part of
the Binance.US research engine, none of it touches data/market.sqlite, and
none of it changes trading behaviour; crypto_research/ remains Binance.US-only
by construction (see safety-check). Cached in data/reference/, same as every
other external source here.

Honest limits: a signal from one exchange's leveraged traders is not
necessarily representative of the whole market; and this repeats the same
"does an extreme reading predict what's next" test already run once on Fear &
Greed, so a similar result here is correlated evidence, not fully independent
confirmation.

    python scripts/leverage_and_flows.py
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REF = Path(__file__).parent.parent / "data/reference"
RESULTS = Path(__file__).parent.parent / "results"


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def fetch_funding_history() -> list[tuple[datetime, float]]:
    """Every BTC perpetual-future funding rate on record, oldest first.

    Source: Deribit, not OKX. OKX's public funding-rate-history endpoint was
    tried first and confirmed working, but its retention is only about 3
    months (a real call to it, one year back, returns HTTP 200 with an empty
    result -- a genuine limit, not a pagination bug, confirmed by testing).
    Three months is too short to say anything about "next 90 days," so
    Deribit's BTC-PERPETUAL was checked next: its funding history starts
    between March and June 2019 (confirmed by probing month by month), giving
    over 7 years -- long enough to actually test something. Windowed by
    calendar month rather than a row-count cursor, since Deribit's endpoint
    takes a start/end timestamp directly rather than a page cursor.
    """
    cache = REF / "deribit_btc_funding_rate.csv"
    if not cache.exists():
        rows: dict[int, float] = {}
        month = date(2019, 6, 1)
        today = date.today()
        while month <= today:
            nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
            start_ms = int(datetime(month.year, month.month, 1, tzinfo=timezone.utc).timestamp() * 1000)
            end_ms = int(datetime(nxt.year, nxt.month, 1, tzinfo=timezone.utc).timestamp() * 1000)
            url = ("https://www.deribit.com/api/v2/public/get_funding_rate_history"
                   f"?instrument_name=BTC-PERPETUAL&start_timestamp={start_ms}"
                   f"&end_timestamp={end_ms}")
            payload = json.loads(get(url))
            for row in payload.get("result", []):
                # Deribit's "interest_8h" is the rate expressed per 8-hour
                # period even though rows are hourly, matching OKX's units.
                rows[int(row["timestamp"])] = float(row["interest_8h"])
            month = nxt
            time.sleep(0.2)
        rows_sorted = sorted(rows.items())
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp_ms", "funding_rate"])
            w.writerows(rows_sorted)
    out = []
    for r in csv.DictReader(cache.open(encoding="utf-8")):
        out.append((datetime.fromtimestamp(int(r["timestamp_ms"]) / 1000, tz=timezone.utc),
                    float(r["funding_rate"])))
    return out


def fetch_stablecoin_supply(asset: str) -> dict[date, float]:
    cache = REF / f"coinmetrics_supply_{asset}.csv"
    if not cache.exists():
        rows, url = [], (
            "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
            f"?assets={asset}&metrics=SplyCur&frequency=1d&start_time=2018-01-01"
            "&page_size=10000"
        )
        while url:
            page = json.loads(get(url))
            rows.extend(page["data"])
            url = page.get("next_page_url")
            time.sleep(0.5)
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "supply"])
            for r in rows:
                if r.get("SplyCur"):
                    w.writerow([r["time"][:10], r["SplyCur"]])
    return {date.fromisoformat(r["date"]): float(r["supply"])
            for r in csv.DictReader(cache.open(encoding="utf-8"))}


def btc_daily_closes() -> dict[date, float]:
    return {date.fromisoformat(r["date"]): float(r["value"])
            for r in csv.DictReader((REF / "btc_long.csv").open(encoding="utf-8"))}


def forward_return(prices: dict[date, float], start: date, days: int) -> float | None:
    all_days = sorted(prices)
    if start not in prices:
        cands = [d for d in all_days if d >= start]
        if not cands:
            return None
        start = cands[0]
    target = start + timedelta(days=days)
    later = [d for d in all_days if d >= target]
    if not later:
        return None
    return prices[later[0]] / prices[start] - 1


def main() -> None:
    btc = btc_daily_closes()

    # -- 1. Funding rate -----------------------------------------------------
    funding = fetch_funding_history()
    print("1. LEVERAGE COST (Deribit BTC perpetual funding rate, paid continuously)\n")
    print(f"  history: {funding[0][0].date()} to {funding[-1][0].date()} "
          f"({len(funding)} readings)")

    # Collapse to one representative daily rate (the day's average of however
    # many 8h readings fell on it) so this lines up with Bitcoin's daily close.
    daily_funding: dict[date, list[float]] = {}
    for ts, rate in funding:
        daily_funding.setdefault(ts.date(), []).append(rate)
    daily_funding_avg = {d: statistics.mean(v) for d, v in daily_funding.items()}

    rates_sorted = sorted(daily_funding_avg.values())
    today_rate = daily_funding_avg[max(daily_funding_avg)]
    below = sum(1 for r in rates_sorted if r < today_rate) / len(rates_sorted)
    print(f"  today's rate: {today_rate*100:.4f}% per funding interval -- "
          f"higher than {below*100:.0f}% of days on record\n")

    n = len(rates_sorted)
    thresholds = {
        "most negative 10% (crowded shorts)": rates_sorted[:n // 10],
        "middle 80% (no crowd either way)": rates_sorted[n // 10: n - n // 10],
        "most positive 10% (crowded longs)": rates_sorted[n - n // 10:],
    }
    print(f"  {'funding-rate regime':<36}{'days':>6}{'next 30d avg':>14}{'next 30d fell':>15}"
          f"{'next 90d avg':>14}{'next 90d fell':>15}")
    for label, bucket in thresholds.items():
        bucket_set = set(bucket)
        days_in_bucket = [d for d, r in daily_funding_avg.items() if r in bucket_set]
        r30 = [x for x in (forward_return(btc, d, 30) for d in days_in_bucket) if x is not None]
        r90 = [x for x in (forward_return(btc, d, 90) for d in days_in_bucket) if x is not None]
        if r30:
            f30 = f"{sum(1 for x in r30 if x < 0)}/{len(r30)}"
            f90 = f"{sum(1 for x in r90 if x < 0)}/{len(r90)}" if r90 else "n/a"
            a90 = f"{statistics.mean(r90)*100:+.1f}%" if r90 else "n/a"
            print(f"  {label:<36}{len(days_in_bucket):>6}{statistics.mean(r30)*100:>+13.1f}%"
                  f"{f30:>15}{a90:>14}{f90:>15}")

    # -- 2. Stablecoin supply -------------------------------------------------
    print("\n\n2. NEW MONEY ARRIVING (combined USDT + USDC supply growth)\n")
    usdt = fetch_stablecoin_supply("usdt")
    usdc = fetch_stablecoin_supply("usdc")
    common_days = sorted(set(usdt) & set(usdc))
    combined = {d: usdt[d] + usdc[d] for d in common_days}
    print(f"  history: {common_days[0]} to {common_days[-1]}")
    now_supply = combined[common_days[-1]]
    print(f"  combined supply today: ${now_supply/1e9:.1f}bn")

    monthly_growth: dict[date, float] = {}
    month_starts = sorted({d.replace(day=1) for d in common_days})
    for a, b in zip(month_starts, month_starts[1:]):
        near_a = max((d for d in common_days if d <= a), default=None)
        near_b = max((d for d in common_days if d <= b), default=None)
        if near_a and near_b and combined[near_a] > 0:
            monthly_growth[b] = combined[near_b] / combined[near_a] - 1

    growth_vals = sorted(monthly_growth.values())
    n = len(growth_vals)
    buckets2 = {
        "supply shrank (bottom 20% of months)": growth_vals[: n // 5],
        "supply grew fastest (top 20% of months)": growth_vals[n - n // 5:],
    }
    print(f"\n  {'stablecoin-supply regime':<40}{'months':>7}{'next 30d BTC avg':>18}{'next 30d fell':>15}")
    for label, bucket in buckets2.items():
        bucket_set = set(bucket)
        months_in_bucket = [d for d, g in monthly_growth.items() if g in bucket_set]
        r30 = [x for x in (forward_return(btc, d, 30) for d in months_in_bucket) if x is not None]
        if r30:
            print(f"  {label:<40}{len(months_in_bucket):>7}{statistics.mean(r30)*100:>+17.1f}%"
                  f"{f'{sum(1 for x in r30 if x<0)}/{len(r30)}':>15}")

    # -- 3. Case-study record --------------------------------------------------
    print("\n\n3. THE BIGGEST DRAWDOWNS IN OUR OWN DATA, AND WHAT DROVE THEM\n")
    print("  (from results/risk_profile.csv and event_study.py; qualitative,")
    print("   cross-referenced against public reporting, not measured here)\n")
    cases = [
        ("2025-10-10 to 2025-10-11", "BTC -12.5% close-to-low in our own data (~-14% intraday, widely reported)",
         "THE LARGEST LEVERAGE LIQUIDATION EVENT ON RECORD. Trump's announcement of a "
         "100% tariff on Chinese imports hit equities and commodities first; crypto was "
         "sitting near record-high open interest and crowded long positioning and became, "
         "in one analyst's words, 'the perfect pressure point.' Over $19bn in leveraged "
         "positions were forcibly closed, more than 1.6 million trading accounts "
         "liquidated -- about 9x the February 2025 crash and 19x the FTX collapse. "
         "Cross-checked against this project's own Binance.US data: BTCUSDT closed "
         "$124,798 on Oct 6 and fell to a low of $109,278 by Oct 11, independently "
         "confirming an event of this scale really happened, on these dates, on this "
         "exchange -- not just in the news reports. Sources: CoinDesk, Forbes, CCN, "
         "FTI Consulting (links in results/reference_verification.md addendum below)."),
        ("2021-05-19 to 2021-05-23", "BTC -30% in 4 days",
         "China's mining-ban headline hit a market already record-long on leverage. "
         "$8.6bn in leveraged positions were forcibly closed in 24 hours -- a record at "
         "the time, since surpassed by October 2025 above. Amplified an initial "
         "news-driven drop into a much larger one."),
        ("2022-05-07 to 2022-05-12", "LUNA itself fell essentially to zero",
         "Not a futures-leverage cascade -- a stablecoin (UST) losing its peg triggered "
         "a bank-run-style redemption spiral. It matters for this project's data because "
         "it triggered broad de-risking and leveraged liquidations across the wider "
         "market in its wake, visible in results/risk_profile.csv as one of the worst "
         "stretches in the whole six-year window."),
        ("2022-11-08 to 2022-11-09", "FTX collapse, BTC -14% in a day"),
    ]
    for case in cases:
        window, size = case[0], case[1]
        note = case[2] if len(case) > 2 else (
            "FTX's own affiliated trading firm (Alameda) was a major leveraged market "
            "participant; its insolvency and FTX's own halted withdrawals forced "
            "liquidations across every exchange with exposure to either firm."
        )
        print(f"  {window}  ({size})")
        print(f"    {note}\n")
    print("  Pattern across all four: the INITIAL trigger was news, tariffs, or a")
    print("  stablecoin failure -- not leverage itself. But every time, a market already")
    print("  carrying a lot of leveraged, one-directional betting turned that trigger into")
    print("  a much larger move than the news alone would suggest, because forced closures")
    print("  beget more forced closures. The October 2025 event is the clearest illustration:")
    print("  the trigger (a tariff announcement) had nothing to do with crypto at all.")


if __name__ == "__main__":
    main()
