"""Has diversification WITHIN crypto gotten weaker over time?

big_forces.py already found one flat, whole-period correlation number per coin
(0.20 to 0.74 against Bitcoin). A single number over six years can hide a lot:
two coins could have started independent and drifted into lockstep, or the
other way around. This tracks the SAME relationship in 90-day rolling windows
instead, to see whether it has trended, and answers the practical question:
if you held several coins instead of one, did that actually cushion you more
in EARLY years than it does now?

Uses the same 7 Binance.US coins as big_forces.py, same six-year range.

    python scripts/rolling_correlation.py
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository
from crypto_research.timeutil import DAY_MS, parse_time, to_iso

COINS = ["BTC", "ETH", "XRP", "SOL", "ZEC", "DOGE", "BNB"]
WINDOW_DAYS = 90


def daily_returns(closes: dict[int, float], times: list[int]) -> dict[int, float]:
    out = {}
    for a, b in zip(times, times[1:]):
        if a in closes and b in closes and closes[a] > 0:
            out[b] = closes[b] / closes[a] - 1
    return out


def rolling_corr(a: dict[int, float], b: dict[int, float], centers: list[int],
                 window_ms: int) -> dict[int, float]:
    out = {}
    for c in centers:
        window = [t for t in a if c - window_ms <= t <= c]
        xs = [a[t] for t in window if t in b]
        ys = [b[t] for t in window if t in b]
        if len(xs) >= 30:
            try:
                out[c] = statistics.correlation(xs, ys)
            except statistics.StatisticsError:
                pass
    return out


def main() -> None:
    with open_database(Path(__file__).parent.parent / "data/market.sqlite") as db:
        repo = BarRepository(db)
        closes: dict[str, dict[int, float]] = {}
        for c in COINS:
            bars = repo.load(f"{c}USDT", "1d", start_time=parse_time("2020-10-01"),
                             end_time=parse_time("2026-09-26"))
            closes[c] = {b.open_time: float(b.close) for b in bars}

    all_times = sorted(closes["BTC"])
    returns = {c: daily_returns(closes[c], all_times) for c in COINS}
    window_ms = WINDOW_DAYS * DAY_MS
    centers = all_times[::7]  # weekly sampling of the rolling window, plenty dense

    print(f"90-day rolling correlation with Bitcoin, sampled weekly, {len(centers)} points\n")

    eras = [
        ("2020-10-01", "2022-04-01", "first 18 months"),
        ("2022-04-01", "2024-04-01", "middle 24 months"),
        ("2024-04-01", "2026-09-26", "most recent ~30 months"),
    ]
    print(f"{'coin':<6}" + "".join(f"{label:>24}" for _, _, label in eras) + f"{'trend':>12}")
    era_bounds = [(parse_time(s), parse_time(e)) for s, e, _ in eras]

    for coin in COINS:
        if coin == "BTC":
            continue
        rc = rolling_corr(returns["BTC"], returns[coin], centers, window_ms)
        row = []
        era_avgs = []
        for lo, hi in era_bounds:
            vals = [v for t, v in rc.items() if lo <= t <= hi]
            avg = statistics.mean(vals) if vals else None
            era_avgs.append(avg)
            row.append("n/a" if avg is None else f"{avg:+.2f}")
        trend = ""
        if era_avgs[0] is not None and era_avgs[-1] is not None:
            trend = "rising" if era_avgs[-1] > era_avgs[0] + 0.05 else \
                    "falling" if era_avgs[-1] < era_avgs[0] - 0.05 else "flat"
        print(f"{coin:<6}" + "".join(f"{r:>24}" for r in row) + f"{trend:>12}")

    print("\n\nWHAT THIS MEANS FOR HOLDING SEVERAL COINS INSTEAD OF ONE\n")
    print("A correlation near +1.00 means a second coin moves in lockstep with Bitcoin --")
    print("owning both barely spreads your risk. A correlation well below +1.00 means it")
    print("does not, and owning both genuinely diversifies you some of the time.")
    print()
    all_recent = []
    for coin in COINS:
        if coin == "BTC":
            continue
        rc = rolling_corr(returns["BTC"], returns[coin], centers, window_ms)
        recent = [v for t, v in rc.items() if t >= era_bounds[-1][0]]
        if recent:
            all_recent.append((coin, statistics.mean(recent)))
    avg_recent = statistics.mean(v for _, v in all_recent)
    print(f"Average correlation with Bitcoin across the other 6 coins, most recent era: "
          f"{avg_recent:+.2f}")
    print("If this number is HIGHER than in the earliest era, the practical diversification")
    print("benefit of holding several coins instead of concentrating in Bitcoin has been")
    print("shrinking, not growing, as the market has matured.")


if __name__ == "__main__":
    main()
