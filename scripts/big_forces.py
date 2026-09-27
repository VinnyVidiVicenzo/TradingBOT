"""Analyst step 2: what outside forces move crypto?

Monthly returns, because daily moves are dominated by noise and different
markets close at different times of day.

  1. Stocks  -- do crypto and stocks move together, and does crypto hold up or
                fall harder in the stock market's worst months?
  2. Rates   -- how does crypto do when short-term interest rates rise or fall?
  3. Dollar  -- how does crypto do when the US dollar strengthens or weakens?
  4. Coins   -- do the other coins just follow Bitcoin?

"Move-together score" is the correlation of monthly returns: +1 means they rise
and fall together every month, 0 means no relationship, -1 means opposites.
A score around 0.3 is weak; 0.6 or more is strong.

Sources: Bitcoin since Sept 2014, stock fund (SPY), 3-month T-bill yield and the
US Dollar Index from Yahoo Finance (data/reference/); the seven coins from our
own Binance.US data, Oct 2020 onward.

Limits: relationships measured over twelve years can and do change; a pattern
here is a description of the past, not a rule. And a relationship is not a
cause -- crypto and stocks both react to the same news about the economy.

    python scripts/big_forces.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository
from crypto_research.timeutil import parse_time, to_iso

REF = Path(__file__).parent.parent / "data/reference"
COINS = ["BTC", "ETH", "XRP", "SOL", "ZEC", "DOGE", "BNB"]


def load_ref(name: str) -> dict[date, float]:
    return {date.fromisoformat(r["date"]): float(r["value"])
            for r in csv.DictReader((REF / name).open(encoding="utf-8"))}


def month_end(series: dict[date, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in sorted(series):
        out[d.strftime("%Y-%m")] = series[d]
    return out


def monthly_returns(series: dict[date, float]) -> dict[str, float]:
    m = month_end(series)
    keys = sorted(m)
    return {k: m[k] / m[p] - 1 for p, k in zip(keys, keys[1:])}


def corr(a: dict[str, float], b: dict[str, float]) -> tuple[float, int]:
    keys = sorted(set(a) & set(b))
    if len(keys) < 6:
        return float("nan"), len(keys)
    return statistics.correlation([a[k] for k in keys], [b[k] for k in keys]), len(keys)


def pct(x: float) -> str:
    return f"{x * 100:+.1f}%"


def main() -> None:
    btc = monthly_returns(load_ref("btc_long.csv"))
    spy = monthly_returns(load_ref("spy_long.csv"))
    dollar = monthly_returns(load_ref("dollar_index.csv"))
    rate_level = month_end(load_ref("tbill_long.csv"))
    rate_keys = sorted(rate_level)
    rate_change = {k: rate_level[k] - rate_level[p] for p, k in zip(rate_keys, rate_keys[1:])}

    coins: dict[str, dict[str, float]] = {}
    with open_database(Path(__file__).parent.parent / "data/market.sqlite") as db:
        repo = BarRepository(db)
        for c in COINS:
            bars = repo.load(f"{c}USDT", "1d", start_time=parse_time("2020-09-01"),
                             end_time=parse_time("2026-09-26"))
            coins[c] = monthly_returns(
                {date.fromisoformat(to_iso(b.open_time)[:10]): float(b.close) for b in bars}
            )

    btc = {k: v for k, v in btc.items() if k >= "2014-10" and k <= "2026-09"}
    shared = sorted(set(btc) & set(spy))

    # ---- 1. stocks -----------------------------------------------------
    print("1. STOCKS  (Bitcoin since late 2014, monthly)\n")
    c, n = corr(btc, spy)
    print(f"  Move-together score, Bitcoin vs stock fund, whole period: {c:+.2f}  ({n} months)")
    print("  By period:")
    for start, end, label in (("2014-10", "2019-12", "2014-2019"), ("2020-01", "2022-12", "2020-2022"),
                              ("2023-01", "2026-09", "2023-2026")):
        a = {k: v for k, v in btc.items() if start <= k <= end}
        c, n = corr(a, spy)
        print(f"    {label}: {c:+.2f}  ({n} months)")

    worst = sorted(shared, key=lambda k: spy[k])[:12]
    down = sum(1 for k in worst if btc[k] < 0)
    print(f"\n  The stock fund's 12 worst months since 2014 (average {pct(statistics.mean(spy[k] for k in worst))}):")
    print(f"    Bitcoin fell in {down} of 12, average {pct(statistics.mean(btc[k] for k in worst))}")
    for k in sorted(worst):
        print(f"      {k}: stocks {pct(spy[k]):>7}   Bitcoin {pct(btc[k]):>7}")
    best = sorted(shared, key=lambda k: spy[k])[-12:]
    print(f"  The stock fund's 12 best months: Bitcoin rose in "
          f"{sum(1 for k in best if btc[k] > 0)} of 12, average {pct(statistics.mean(btc[k] for k in best))}")

    # ---- 2. rates ------------------------------------------------------
    print("\n\n2. INTEREST RATES  (3-month Treasury yield, monthly change)\n")
    rk = sorted(set(btc) & set(rate_change) & set(spy))
    groups = {
        "rates rose by 0.25 points or more": [k for k in rk if rate_change[k] >= 0.25],
        "rates roughly flat": [k for k in rk if -0.25 < rate_change[k] < 0.25],
        "rates fell by 0.25 points or more": [k for k in rk if rate_change[k] <= -0.25],
    }
    print(f"  {'':<36}{'months':>7}{'Bitcoin avg':>13}{'stocks avg':>12}")
    for label, ks in groups.items():
        if ks:
            print(f"  {label:<36}{len(ks):>7}{pct(statistics.mean(btc[k] for k in ks)):>13}"
                  f"{pct(statistics.mean(spy[k] for k in ks)):>12}")
    hike = [k for k in rk if "2022-03" <= k <= "2023-07"]
    print(f"\n  During the big 2022-23 rate rises (Mar 2022 - Jul 2023, {len(hike)} months):")
    def compound(r, ks):
        v = 1.0
        for k in ks:
            v *= 1 + r[k]
        return v - 1
    print(f"    Bitcoin {pct(compound(btc, hike))}, stock fund {pct(compound(spy, hike))} overall")
    c, n = corr(btc, rate_change)
    print(f"  Move-together score, Bitcoin vs rate changes: {c:+.2f}")

    # ---- 3. dollar -----------------------------------------------------
    print("\n\n3. THE US DOLLAR  (Dollar Index, monthly)\n")
    dk = sorted(set(btc) & set(dollar))
    for label, ks in (("dollar strengthened 2%+", [k for k in dk if dollar[k] >= 0.02]),
                      ("dollar weakened 2%+", [k for k in dk if dollar[k] <= -0.02])):
        print(f"  {label:<26}{len(ks):>4} months   Bitcoin avg {pct(statistics.mean(btc[k] for k in ks))}"
              f"   fell in {sum(1 for k in ks if btc[k] < 0)} of them")
    c, n = corr(btc, dollar)
    print(f"  Move-together score, Bitcoin vs dollar: {c:+.2f}")

    # ---- 4. coins ------------------------------------------------------
    print("\n\n4. DO THE OTHER COINS JUST FOLLOW BITCOIN?  (Oct 2020 - Sep 2026)\n")
    print(f"  {'coin':<6}{'score vs Bitcoin':>18}{'score vs stocks':>17}{'worst month':>13}"
          f"{'when Bitcoin fell 20%+':>24}")
    btc_ours = coins["BTC"]
    crashes = [k for k in btc_ours if btc_ours[k] <= -0.20]
    for cn in COINS:
        cb, _ = corr(coins[cn], btc_ours)
        cs, _ = corr(coins[cn], spy)
        w = min(coins[cn].values())
        crash_avg = statistics.mean(coins[cn][k] for k in crashes if k in coins[cn])
        print(f"  {cn:<6}{cb:>+18.2f}{cs:>+17.2f}{pct(w):>13}{pct(crash_avg):>24}")
    print(f"\n  ({len(crashes)} months where Bitcoin fell 20% or more: {', '.join(sorted(crashes))})")


if __name__ == "__main__":
    main()
