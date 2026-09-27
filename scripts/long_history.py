"""Longer history: do the 2020-26 conclusions survive 26 years of stocks and 12 of Bitcoin?

2020-26 had one crypto boom-and-bust and no big stock crash, which flattered the
stock fund and tested crypto through a single cycle. This reruns the key studies
on longer data:

  Part 1  stocks since 2000 -- includes the 2000-02 and 2008-09 crashes
  Part 2  Bitcoin + stocks + savings since September 2014 -- three or four crypto cycles

Sources (Yahoo Finance, cached in data/reference/, kept OUT of the Binance.US store):
  SPY dividend-adjusted, 3-month Treasury bill yield (^IRX), and BTC-USD.

Honest limits:
  * Crypto here is Bitcoin alone. Older data does not have today's range of coins,
    and using a basket would need coin lists nobody could have known at the time.
  * BTC-USD before about 2019 is assembled from exchanges that no longer exist and
    is less reliable than Binance.US data. It is checked against our Binance.US
    prices where the two overlap.
  * Bitcoin pays the same 0.7% trading cost as before. The fund and cash trade free.

Declared up front: mixes from series.MIXES with crypto = Bitcoin; futures use
30-day blocks, 5,000 paths, seed 20260927, scenarios A (past reshuffled) and
B (extra growth over savings halved).

    python scripts/long_history.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from possible_futures import halve_extra_growth, study
from series import MIXES, pain, simulate

REF = Path(__file__).parent.parent / "data/reference"
STAKE = 200.0


def load(name: str) -> dict[date, float]:
    return {date.fromisoformat(r["date"]): float(r["value"])
            for r in csv.DictReader((REF / name).open(encoding="utf-8"))}


def calendar(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def price_returns(prices: dict[date, float], days: list[date]) -> list[float]:
    out, prev = [], None
    for d in days:
        if d in prices:
            out.append(0.0 if prev is None else prices[d] / prev - 1)
            prev = prices[d]
        else:
            out.append(0.0)
    return out


def cash_returns(yields: dict[date, float], days: list[date]) -> list[float]:
    known = sorted(yields)
    out, cur, i = [], yields[known[0]], 0
    for d in days:
        while i < len(known) and known[i] <= d:
            cur = yields[known[i]]
            i += 1
        out.append(cur / 100 / 365)
    return out


def rolling(mixes, series, days, years_list):
    rows = []
    for years in years_list:
        length = 365 * years
        starts = [i for i, d in enumerate(days) if d.day == 1 and i + length <= len(days)]
        for i in starts:
            w = slice(i, i + length)
            savings = simulate(MIXES["Savings account only"], series["crypto"][w],
                               series["stocks"][w], series["cash"][w])[-1]
            for name, mix in mixes.items():
                facts = pain(simulate(mix, series["crypto"][w], series["stocks"][w],
                                      series["cash"][w]))
                facts.update(years=years, mix=name, start=days[i].isoformat(),
                             beat=facts["end"] > savings + 0.005)
                rows.append(facts)
    return rows


def print_rolling(rows, years_list, title):
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")
    for years in years_list:
        group = [r for r in rows if r["years"] == years]
        if not group:
            continue
        n = len({r["start"] for r in group})
        print(f"\n  held {years} years, {n} start months")
        print(f"  {'mix':<26}{'typical':>9}{'worst start':>13}{'lost money':>12}"
              f"{'beat savings':>14}{'worst drop':>12}{'longest in red':>16}")
        for name in dict.fromkeys(r["mix"] for r in group):
            g = [r for r in group if r["mix"] == name]
            ends = sorted(r["end"] for r in g)
            lost = sum(1 for r in g if r["end"] < STAKE)
            beat = "--" if name == "Savings account only" else f"{sum(r['beat'] for r in g)}/{len(g)}"
            print(f"  {name:<26}{ends[len(ends)//2]:>9.0f}{ends[0]:>13.0f}"
                  f"{f'{lost}/{len(g)}':>12}{beat:>14}"
                  f"{f'-{max(r['worst_drop'] for r in g)*100:.0f}%':>12}"
                  f"{f'{max(r['longest_in_red'] for r in g)/365:.1f} yrs':>16}")


def print_futures(rows, scenario, years):
    group = [r for r in rows if r["scenario"] == scenario and r["years"] == years]
    print(f"\n  {scenario}, held {years} years, 5,000 futures")
    print(f"  {'mix':<26}{'typical':>9}{'bad 1-in-20':>13}{'lose money':>12}"
          f"{'fall by half':>14}{'beat savings':>14}")
    for r in group:
        beat = "--" if r["mix"] == "Savings account only" else f"{r['chance_beat_savings']*100:.0f}%"
        print(f"  {r['mix']:<26}{r['median']:>9.0f}{r['bad_1_in_20']:>13.0f}"
              f"{r['chance_lose_money']*100:>11.0f}%{r['chance_fall_by_half']*100:>13.0f}%{beat:>14}")


def main() -> None:
    spy, btc, tbill = load("spy_long.csv"), load("btc_long.csv"), load("tbill_long.csv")

    # ---- checks before trusting anything --------------------------------
    print("CHECKS")
    from crypto_research.storage.database import open_database
    from crypto_research.storage.repositories import BarRepository
    from crypto_research.timeutil import parse_time, to_iso
    with open_database(Path(__file__).parent.parent / "data/market.sqlite") as db:
        bars = BarRepository(db).load("BTCUSDT", "1d", start_time=parse_time("2021-01-01"),
                                      end_time=parse_time("2026-09-26"))
    ours = {date.fromisoformat(to_iso(b.open_time)[:10]): float(b.close) for b in bars}
    common = sorted(set(ours) & set(btc))
    gaps = [abs(btc[d] / ours[d] - 1) for d in common]
    print(f"  Bitcoin: Yahoo vs our Binance.US closes on {len(common)} shared days -- "
          f"median difference {statistics.median(gaps)*100:.2f}%, largest {max(gaps)*100:.1f}%")
    sp = [(d, spy[d]) for d in sorted(spy) if date(2007, 1, 1) <= d <= date(2009, 12, 31)]
    peak = max(sp[:300], key=lambda p: p[1])
    low = min((p for p in sp if p[0] > peak[0]), key=lambda p: p[1])
    print(f"  Stock fund 2008 crash: {peak[0]} -> {low[0]}, fell {(1-low[1]/peak[1])*100:.0f}% "
          f"(dividends included; the index alone fell about 57%)")

    # ---- part 1: stocks since 2000 ---------------------------------------
    days1 = calendar(date(2000, 1, 3), date(2026, 9, 25))
    s1 = {"stocks": price_returns(spy, days1), "cash": cash_returns(tbill, days1),
          "crypto": [0.0] * len(days1)}
    mixes1 = {k: MIXES[k] for k in ("Savings account only", "Stock fund only")}
    rows1 = rolling(mixes1, s1, days1, (1, 3, 5, 10))
    print_rolling(rows1, (1, 3, 5, 10), "PART 1: STOCK FUND vs SAVINGS SINCE 2000 (two big crashes included)")

    f1 = study(s1, "stocks since 2000", 30, mixes1, horizons=(1, 5, 10))
    print("\n  Possible futures built from 2000-2026:")
    for y in (1, 5, 10):
        print_futures(f1, "stocks since 2000", y)

    # ---- part 2: Bitcoin + stocks + savings since 2014 --------------------
    days2 = calendar(date(2014, 9, 18), date(2026, 9, 25))
    s2 = {"crypto": price_returns(btc, days2), "stocks": price_returns(spy, days2),
          "cash": cash_returns(tbill, days2)}
    mixes2 = {k.replace("crypto", "Bitcoin"): v for k, v in MIXES.items()}
    rows2 = rolling(mixes2, s2, days2, (1, 3, 5))
    print_rolling(rows2, (1, 3, 5), "PART 2: BITCOIN MIXES SINCE SEPTEMBER 2014 (several crypto cycles)")

    half = {"crypto": halve_extra_growth(s2["crypto"], s2["cash"]),
            "stocks": halve_extra_growth(s2["stocks"], s2["cash"]), "cash": s2["cash"]}
    f2 = study(s2, "A like 2014-26", 30, MIXES, horizons=(5,))
    f2 += study(half, "B half the growth", 30, MIXES, horizons=(5,))
    for r in f2:
        r["mix"] = r["mix"].replace("crypto", "Bitcoin")
    print("\n  Possible futures built from 2014-2026:")
    print_futures(f2, "A like 2014-26", 5)
    print_futures(f2, "B half the growth", 5)

    out = Path(__file__).parent.parent / "results/long_history.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["part", "years", "mix", "start", "end", "worst_drop", "longest_in_red", "beat_savings"])
        for part, rows in (("stocks_since_2000", rows1), ("bitcoin_since_2014", rows2)):
            for r in rows:
                w.writerow([part, r["years"], r["mix"], r["start"], r["end"],
                            r["worst_drop"], r["longest_in_red"], r["beat"]])
    print(f"\nrolling results written to {out}")


if __name__ == "__main__":
    main()
