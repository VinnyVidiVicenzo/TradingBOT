"""Study 3: thousands of possible futures instead of one past.

Chops the six years of daily returns into 30-day blocks, shuffles them, and
stitches them back together into thousands of new paths. Each path behaves like
the real past -- same size of swings, same way crashes cluster, same way crypto,
stocks and interest rates moved together on a given day -- but in a different
order. Every mix is run on the SAME shuffled days, so comparisons are fair.

Declared before running:
  block length   30 days (a 90-day sensitivity check is reported too)
  paths          5,000 per horizon
  horizons       1, 3 and 5 years
  seed           20260927, so results are exactly repeatable

Two scenarios, because 2020-26 was an unusually good stretch for both stocks
(about 2.5x) and crypto (the basket about 5.6x), and a plain reshuffle inherits
that good fortune:
  A "like 2020-26"      the past, reshuffled
  B "half the growth"   same swings, but the extra growth crypto and stocks
                        earned above a savings account is cut in half

What this cannot do: invent something that never happened in the six years --
an exchange collapse that takes your coins, a decade-long stock slump, rates
far outside 0-5%. The odds below are odds within the world of 2020-26.

    python scripts/possible_futures.py
"""

from __future__ import annotations

import csv
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from series import CRYPTO_TRADE_COST, MIXES, build

SEED = 20260927
PATHS = 5000
BLOCK = 30
HORIZONS_YEARS = (1, 3, 5)
STAKE = 200.0
SLEEVES = ("crypto", "stocks", "cash")


def halve_extra_growth(returns: list[float], cash: list[float]) -> list[float]:
    excess = statistics.mean(returns) - statistics.mean(cash)
    return [r - excess / 2 for r in returns]


def sample_path(rng: random.Random, n_days: int, length: int, block: int) -> list[int]:
    out: list[int] = []
    while len(out) < length:
        start = rng.randrange(0, n_days - block)
        out.extend(range(start, start + block))
    return out[:length]


def run_mix(mix: dict[str, float], r: dict[str, list[float]], idx: list[int]):
    """Value path for one mix on one shuffled path, rebalanced yearly."""
    names = list(mix)
    weights = [mix[k] for k in names]
    series = [r[k] for k in names]
    has_crypto = "crypto" in mix
    ci = names.index("crypto") if has_crypto else -1
    sleeves = [STAKE * w for w in weights]
    if has_crypto:
        sleeves[ci] *= 1 - CRYPTO_TRADE_COST
    peak = STAKE
    worst = 0.0
    for day, i in enumerate(idx):
        for j in range(len(sleeves)):
            sleeves[j] *= 1 + series[j][i]
        total = sum(sleeves)
        if day and day % 365 == 0 and len(sleeves) > 1:
            if has_crypto:
                total -= abs(total * weights[ci] - sleeves[ci]) * CRYPTO_TRADE_COST
            sleeves = [total * w for w in weights]
        if total > peak:
            peak = total
        drop = (peak - total) / peak
        if drop > worst:
            worst = drop
    return total, worst


def study(returns: dict[str, list[float]], label: str, block: int,
          mixes: dict[str, dict[str, float]], horizons=HORIZONS_YEARS) -> list[dict]:
    rng = random.Random(SEED)
    n = len(returns["cash"])
    rows = []
    for years in horizons:
        length = 365 * years
        results = {name: [] for name in mixes}
        for _ in range(PATHS):
            idx = sample_path(rng, n, length, block)
            savings_end, _ = run_mix(MIXES["Savings account only"], returns, idx)
            for name, mix in mixes.items():
                end, worst = run_mix(mix, returns, idx)
                results[name].append((end, worst, end > savings_end + 0.005))
        for name, outcomes in results.items():
            ends = sorted(o[0] for o in outcomes)
            rows.append({
                "scenario": label, "block": block, "years": years, "mix": name,
                "median": ends[len(ends) // 2],
                "bad_1_in_20": ends[int(0.05 * len(ends))],
                "good_1_in_20": ends[int(0.95 * len(ends))],
                "chance_lose_money": sum(1 for e in ends if e < STAKE) / len(ends),
                "chance_fall_by_half": sum(1 for o in outcomes if o[1] >= 0.5) / len(ends),
                "chance_beat_savings": sum(1 for o in outcomes if o[2]) / len(ends),
            })
    return rows


def print_table(rows: list[dict], scenario: str, years: int) -> None:
    group = [r for r in rows if r["scenario"] == scenario and r["years"] == years
             and r["block"] == BLOCK]
    print(f"\n--- {scenario}: held {years} year{'s' if years > 1 else ''}, $200, "
          f"{PATHS:,} possible futures ---")
    print(f"{'mix':<26}{'typical':>9}{'bad 1-in-20':>13}{'good 1-in-20':>14}"
          f"{'lose money':>12}{'fall by half':>14}{'beat savings':>14}")
    print("-" * 102)
    for r in group:
        beat = "--" if r["mix"] == "Savings account only" else f"{r['chance_beat_savings']*100:.0f}%"
        print(f"{r['mix']:<26}{r['median']:>9.0f}{r['bad_1_in_20']:>13.0f}"
              f"{r['good_1_in_20']:>14.0f}{r['chance_lose_money']*100:>11.0f}%"
              f"{r['chance_fall_by_half']*100:>13.0f}%{beat:>14}")


def main() -> None:
    s = build()
    past = {"crypto": s.crypto, "stocks": s.stocks, "cash": s.cash}
    half = {
        "crypto": halve_extra_growth(s.crypto, s.cash),
        "stocks": halve_extra_growth(s.stocks, s.cash),
        "cash": s.cash,
    }

    rows = []
    rows += study(past, "A like 2020-26", BLOCK, MIXES)
    rows += study(half, "B half the growth", BLOCK, MIXES)

    key = {k: MIXES[k] for k in ("Stock fund only", "Fund 90% + crypto 10%", "Crypto only")}
    sens = study(past, "A like 2020-26", 90, key, horizons=(3,))
    rows += sens

    out = Path(__file__).parent.parent / "results/possible_futures.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    for scenario in ("A like 2020-26", "B half the growth"):
        for years in HORIZONS_YEARS:
            print_table(rows, scenario, years)

    print("\n--- sensitivity: 3 years, scenario A, 90-day blocks instead of 30 ---")
    for r in sens:
        base = next(b for b in rows if b["block"] == BLOCK and b["years"] == 3
                    and b["scenario"] == "A like 2020-26" and b["mix"] == r["mix"])
        print(f"  {r['mix']:<24} lose money {base['chance_lose_money']*100:.0f}% -> "
              f"{r['chance_lose_money']*100:.0f}%   fall by half "
              f"{base['chance_fall_by_half']*100:.0f}% -> {r['chance_fall_by_half']*100:.0f}%")
    print(f"\nall rows written to {out}")


if __name__ == "__main__":
    main()
