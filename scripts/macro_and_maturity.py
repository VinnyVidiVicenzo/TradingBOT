"""Does Bitcoin behave like gold, has GBTC's history tracked what's known about
it, and has the market actually calmed down as it's grown up?

Three questions, using data already fetched elsewhere in this project plus one
new series (gold):

  1. GOLD. The most common pitch for Bitcoin is "digital gold" -- an inflation
     hedge, uncorrelated with everything else. Tested directly, the same way
     big_forces.py already tested stocks, rates and the dollar.
  2. GBTC. Before 2024, GBTC was the main way to hold Bitcoin exposure in a
     normal brokerage account, and its market price used to swing far above
     and below the value of the Bitcoin it actually held -- a direct,
     historical read of crowd euphoria and panic. GBTC converted to an actual
     ETF on 2024-01-11, after which arbitrage keeps its price at NAV, so any
     premium/discount phenomenon is a pre-2024 story only.

     This script does NOT have GBTC's actual historical NAV-per-share (that
     would need a paid data feed or Grayscale's now-defunct daily disclosures
     archived somewhere), so it cannot report the literal published premium
     percentage. Instead it computes a PROXY: cumulative GBTC return minus
     cumulative Bitcoin return, rebased to start at zero. When GBTC's price
     detaches from NAV, this proxy moves; when arbitrage holds it to NAV (as
     it must post-conversion), the proxy should flatten. The shape is checked
     against what's publicly known about the timeline (premium into 2021,
     discount through 2022, convergence after the 2024 conversion) as a
     sanity check on the proxy itself -- no specific historical premium
     percentage is asserted, since none was independently verified.
  3. MATURING MARKET. Has Bitcoin's day-to-day volatility actually calmed down
     as more institutions have gotten involved, or is it just as wild as ever?
     Rolling 90-day volatility, split by era.

    python scripts/macro_and_maturity.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from big_forces import corr, load_ref, monthly_returns, pct

REF = Path(__file__).parent.parent / "data/reference"


def fetch_gold() -> dict[date, float]:
    """SPDR Gold Shares (GLD): a fund holding physical gold, the same kind of
    proxy this project already uses for stocks (SPY) rather than a raw index."""
    cache = REF / "gld.csv"
    if not cache.exists():
        import json
        import time
        import urllib.request
        from datetime import datetime, timezone

        url = ("https://query1.finance.yahoo.com/v8/finance/chart/GLD"
               "?period1=946684800&period2=1790640000&interval=1d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())["chart"]["result"][0]
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "value"])
            for t, v in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
                if v is not None:
                    d = datetime.fromtimestamp(t, tz=timezone.utc).date()
                    w.writerow([d.isoformat(), v])
        time.sleep(1)
    return {date.fromisoformat(r["date"]): float(r["value"])
            for r in csv.DictReader(cache.open(encoding="utf-8"))}


def fetch_gbtc() -> dict[date, float]:
    cache = REF / "gbtc.csv"
    if not cache.exists():
        import json
        import time
        import urllib.request
        from datetime import datetime, timezone

        url = ("https://query1.finance.yahoo.com/v8/finance/chart/GBTC"
               "?period1=1420070400&period2=1790640000&interval=1d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (research)"})
        r = json.loads(urllib.request.urlopen(req, timeout=60).read().decode())["chart"]["result"][0]
        with cache.open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "value"])
            for t, v in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
                if v is not None:
                    d = datetime.fromtimestamp(t, tz=timezone.utc).date()
                    w.writerow([d.isoformat(), v])
        time.sleep(1)
    return {date.fromisoformat(r["date"]): float(r["value"])
            for r in csv.DictReader(cache.open(encoding="utf-8"))}


def daily_returns(prices: dict[date, float]) -> list[tuple[date, float]]:
    days = sorted(prices)
    out = []
    for a, b in zip(days, days[1:]):
        out.append((b, prices[b] / prices[a] - 1))
    return out


def annualized_vol(returns: list[float]) -> float:
    if len(returns) < 2:
        return float("nan")
    return statistics.stdev(returns) * (365 ** 0.5)


def main() -> None:
    btc = load_ref("btc_long.csv")
    spy = load_ref("spy_long.csv")
    gold = fetch_gold()

    # -- 1. Gold --------------------------------------------------------------
    print("1. IS BITCOIN 'DIGITAL GOLD'?\n")
    btc_m = {k: v for k, v in monthly_returns(btc).items() if k >= "2014-10"}
    gold_m = monthly_returns(gold)
    spy_m = monthly_returns(spy)
    c_gold, n_gold = corr(btc_m, gold_m)
    c_spy, n_spy = corr(btc_m, spy_m)
    print(f"  Move-together score, Bitcoin vs gold:  {c_gold:+.2f}  ({n_gold} months)")
    print(f"  Move-together score, Bitcoin vs stocks: {c_spy:+.2f}  ({n_spy} months)  (for comparison)")

    shared = sorted(set(btc_m) & set(gold_m))
    worst_gold_months = sorted(shared, key=lambda k: gold_m[k])[:12]
    down = sum(1 for k in worst_gold_months if btc_m[k] < 0)
    print(f"\n  In gold's 12 worst months, Bitcoin fell in {down} of 12 "
          f"(average {pct(statistics.mean(btc_m[k] for k in worst_gold_months))}).")
    print("  A real inflation-hedge/safe-haven asset would be expected to move")
    print("  independently of, or opposite to, a falling gold price -- not alongside it.")
    print(f"\n  Verdict: a score of {c_gold:+.2f} is close to zero -- Bitcoin does not")
    print("  meaningfully track gold in either direction. The 'digital gold' pitch is")
    print("  a marketing comparison of what each asset represents, not a demonstrated")
    print("  price relationship in this data.")

    # -- 2. GBTC ---------------------------------------------------------------
    print("\n\n2. THE GBTC PREMIUM/DISCOUNT STORY (approximate reconstruction)\n")
    gbtc = fetch_gbtc()
    common = sorted(set(gbtc) & set(btc))
    print(f"  GBTC price history: {common[0]} to {common[-1]}")
    print("  GBTC converted from a closed-end trust to an actual ETF on 2024-01-11;")
    print("  after that date arbitrage should hold its price close to NAV.\n")

    proxy = 0.0
    proxy_path: dict[date, float] = {}
    for a, b in zip(common, common[1:]):
        gbtc_r = gbtc[b] / gbtc[a] - 1
        btc_r = btc[b] / btc[a] - 1
        proxy += gbtc_r - btc_r
        proxy_path[b] = proxy

    checkpoints = [
        ("2016-01-04", "early, thin trading"),
        ("2017-12-15", "2017 mania peak"),
        ("2020-12-31", "into the 2021 bull run"),
        ("2021-02-19", "still an elevated-premium period"),  # this proxy actually reads
                                                              # higher on 2020-12-31 than
                                                              # here -- not asserting a
                                                              # specific peak date/level
        ("2022-01-03", "start of the 2022 bear market"),
        ("2022-12-30", "widely reported as near the deepest discount, post-FTX"),
        ("2024-01-10", "the day before ETF conversion"),
        ("2024-06-28", "6 months after conversion"),
        (str(common[-1]), "today"),
    ]
    print(f"  {'date':<12}{'proxy (cumulative GBTC-vs-BTC gap)':>36}   context")
    for iso, label in checkpoints:
        d = date.fromisoformat(iso)
        near = max((x for x in proxy_path if x <= d), default=None)
        if near:
            print(f"  {near.isoformat():<12}{proxy_path[near]*100:>+35.1f}%   {label}")

    print("\n  Reading this proxy (it is NOT the literal published premium percentage,")
    print("  only cumulative return drift, which behaves similarly while GBTC could not")
    print("  be redeemed for its underlying Bitcoin):")
    pre_conversion = {d: v for d, v in proxy_path.items() if d < date(2024, 1, 11)}
    post_conversion = {d: v for d, v in proxy_path.items() if d >= date(2024, 1, 11)}
    if pre_conversion and post_conversion:
        pre_range = max(pre_conversion.values()) - min(pre_conversion.values())
        post_range = max(post_conversion.values()) - min(post_conversion.values())
        print(f"    pre-conversion swing in the proxy:  {pre_range*100:.0f} percentage points")
        print(f"    post-conversion swing in the proxy: {post_range*100:.0f} percentage points")
        print("  The proxy should swing far more before conversion than after, if it is")
        print("  correctly picking up the premium/discount phenomenon that ETF arbitrage")
        print("  eliminated. " + ("It does." if pre_range > post_range * 3 else
                                  "It does NOT clearly -- treat this proxy with caution."))

    # -- 3. Maturing market? ----------------------------------------------------
    print("\n\n3. HAS BITCOIN'S VOLATILITY ACTUALLY COME DOWN OVER TIME?\n")
    returns = daily_returns(btc)
    eras = [
        ("2014-10-01", "2017-12-31", "2014-2017 (early)"),
        ("2018-01-01", "2020-12-31", "2018-2020"),
        ("2021-01-01", "2023-12-31", "2021-2023"),
        ("2024-01-01", "2026-09-26", "2024-2026 (ETF era)"),
    ]
    print(f"  {'era':<24}{'days':>6}{'annualized volatility':>24}")
    for start, end, label in eras:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
        window = [r for d, r in returns if s <= d <= e]
        if window:
            print(f"  {label:<24}{len(window):>6}{annualized_vol(window)*100:>23.0f}%")
    print("\n  For comparison, the S&P 500's long-run annualized volatility is typically")
    print("  quoted around 15-20%.")


if __name__ == "__main__":
    main()
