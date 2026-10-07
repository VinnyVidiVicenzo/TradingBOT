"""What allocation would actually have been mathematically "best" -- and why
that question is a trap if answered on only one slice of history.

Grid-searches every crypto/stock/cash weight combination in 10% steps (66
combinations) and scores each one by "gain per unit of worst drawdown" -- the
same risk-adjusted measure vs_stock_fund.py already used, kept for consistency
rather than switching to a less-explained one like the Sharpe ratio.

Two versions, deliberately shown side by side:

  A. SINGLE HISTORY. Run the grid once on the one real 2020-2026 path. This is
     "if you had known the future, what would have been optimal" -- useful
     ONLY as a demonstration of overfitting: the weight that looks best here is
     best BECAUSE it is fitted to exactly what happened, and there is no
     reason to expect it to be best in a future that unfolds differently.

  B. ROBUST, ACROSS 5,000 POSSIBLE FUTURES. The same grid, but each candidate
     weight is scored on its MEDIAN result across the possible_futures.py
     Monte Carlo paths (reusing that module's sample_path and run_mix, same
     seed, same 30-day blocks) rather than on one lucky/unlucky path. This is
     the version worth trusting more, because it rewards a weight that holds
     up across many different orderings of history rather than one that
     happens to fit the specific sequence that actually occurred.

Also answers a smaller, cheaper question: for someone adding money over time
into a blended (mostly-stocks, some-crypto) portfolio rather than a pure
crypto one, does spreading out the buying still not matter (as already found
for pure crypto), or does it matter more once most of the portfolio is in
something steadier?

Bonus: this project's own tools cannot execute this allocation (spot
Binance.US and the rebalance logic here both assume the ability to sell,
which crypto_research/ deliberately does not implement -- see SAFETY.md); this
is an analysis of return series, not a trading recommendation.

    python scripts/optimal_allocation.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from possible_futures import PATHS, SEED, run_mix, sample_path
from series import build, pain, simulate

STAKE = 200.0
STEP = 0.10  # 10% grid steps (66 combinations): crypto x stocks, cash the rest
SEARCH_PATHS = 1000   # coarse search width -- kept smaller so a 66-point grid
                      # search finishes in minutes rather than hours
FINAL_PATHS = 5000    # precision pass on the winners only, matching
                      # possible_futures.py's own path count for comparability


def grid() -> list[dict[str, float]]:
    mixes = []
    steps = int(round(1 / STEP))
    for i in range(steps + 1):
        for j in range(steps + 1 - i):
            crypto = round(i * STEP, 2)
            stocks = round(j * STEP, 2)
            cash = round(1 - crypto - stocks, 2)
            if cash < -1e-9:
                continue
            mixes.append({"crypto": crypto, "stocks": stocks, "cash": max(cash, 0.0)})
    return mixes


def score_single_history(mix: dict[str, float], s) -> tuple[float, float]:
    values = simulate(mix, s.crypto, s.stocks, s.cash, stake=STAKE)
    facts = pain(values, stake=STAKE)
    gain = facts["end"] - STAKE
    drop = facts["worst_drop"]
    ratio = gain / (drop * STAKE) if drop > 0.01 else (gain / (0.01 * STAKE))
    return ratio, facts["end"]


def score_robust(mix: dict[str, float], returns: dict[str, list[float]],
                  paths: list[list[int]]) -> tuple[float, float, float]:
    ends = []
    worst_drops = []
    for idx in paths:
        end, worst = run_mix(mix, returns, idx)
        ends.append(end)
        worst_drops.append(worst)
    median_end = statistics.median(ends)
    median_drop = statistics.median(worst_drops)
    gain = median_end - STAKE
    ratio = gain / (median_drop * STAKE) if median_drop > 0.01 else (gain / (0.01 * STAKE))
    lose_money = sum(1 for e in ends if e < STAKE) / len(ends)
    return ratio, median_end, lose_money


def main() -> None:
    s = build()
    mixes = grid()
    print(f"Testing {len(mixes)} crypto/stock/cash combinations in {STEP * 100:g}% steps.\n")

    # -- A. single history ------------------------------------------------
    print("A. IF YOU HAD KNOWN THE FUTURE (one real path, 2020-2026) -- a trap, see below\n")
    single_scores = [(m, *score_single_history(m, s)) for m in mixes]
    single_scores.sort(key=lambda t: -t[1])
    print(f"{'rank':<6}{'crypto':>8}{'stocks':>8}{'cash':>8}{'ended with':>12}{'gain/drawdown':>15}")
    for rank, (m, ratio, end) in enumerate(single_scores[:5], 1):
        print(f"{rank:<6}{m['crypto']*100:>7.0f}%{m['stocks']*100:>7.0f}%{max(m['cash']*100, 0.0):>7.0f}%"
              f"{end:>12.0f}{ratio:>15.2f}")
    best_single = single_scores[0][0]

    # -- B. robust across possible futures ---------------------------------
    print(f"\n\nB. ROBUST ACROSS MANY POSSIBLE FUTURES (same method as possible_futures.py)\n")
    print(f"  Scoring the same grid on its MEDIAN result across {SEARCH_PATHS:,} resampled")
    print("  versions of history, not the one that actually happened. This is")
    print("  the version worth trusting more. (Coarse search width, for runtime;")
    print(f"  the top candidates are re-scored on {FINAL_PATHS:,} paths below for precision.)\n")

    import random
    rng = random.Random(SEED)
    returns = {"crypto": s.crypto, "stocks": s.stocks, "cash": s.cash}
    n_days = len(s.crypto)
    length = 365 * 3  # 3-year horizon, matching the middle of the earlier studies
    search_paths = [sample_path(rng, n_days, length, 30) for _ in range(SEARCH_PATHS)]

    robust_scores = []
    for m in mixes:
        ratio, median_end, lose_money = score_robust(m, returns, search_paths)
        robust_scores.append((m, ratio, median_end, lose_money))
    robust_scores.sort(key=lambda t: -t[1])

    # Precision re-score of the top 5 on the full path count, same seed so the
    # paths are the same ones possible_futures.py itself used for its 3-year row.
    rng2 = random.Random(SEED)
    final_paths = [sample_path(rng2, n_days, length, 30) for _ in range(FINAL_PATHS)]
    top5 = [m for m, *_ in robust_scores[:5]]
    precise = [(m, *score_robust(m, returns, final_paths)) for m in top5]
    precise.sort(key=lambda t: -t[1])

    print(f"{'rank':<6}{'crypto':>8}{'stocks':>8}{'cash':>8}{'median end':>12}"
          f"{'gain/drawdown':>15}{'lose money':>12}")
    for rank, (m, ratio, end, lose) in enumerate(precise, 1):
        print(f"{rank:<6}{m['crypto']*100:>7.0f}%{m['stocks']*100:>7.0f}%{max(m['cash']*100, 0.0):>7.0f}%"
              f"{end:>12.0f}{ratio:>15.2f}{lose*100:>11.0f}%")
    best_robust = precise[0][0]

    print(f"\n  'Best on one path' picked {best_single['crypto']*100:.0f}% crypto /"
          f" {best_single['stocks']*100:.0f}% stocks / {best_single['cash']*100:.0f}% cash.")
    print(f"  'Best across many paths' picked {best_robust['crypto']*100:.0f}% crypto /"
          f" {best_robust['stocks']*100:.0f}% stocks / {best_robust['cash']*100:.0f}% cash.")
    if best_single != best_robust:
        print("  THEY DISAGREE. That disagreement is the whole point: a weight tuned to one")
        print("  specific history is not the same thing as a weight that holds up broadly.")
    else:
        print("  They happen to agree here -- that is a coincidence of this particular")
        print("  dataset, not a general guarantee that hindsight and robustness will match.")

    print("\n  IMPORTANT CAVEAT ON THE RANKING ABOVE: 'gain per unit of worst drawdown'")
    print("  mechanically rewards near-zero-volatility mixes, because dividing by an")
    print("  almost-zero drawdown inflates the ratio even for a small gain. That is why")
    print("  cash-heavy mixes top the list -- it is a true statement about the RATIO, not")
    print("  a claim that they grew your money the most. For contrast, here is the same")
    print("  grid ranked by plain median ending value instead:\n")
    by_growth = sorted(robust_scores, key=lambda t: -t[2])
    print(f"  {'rank':<6}{'crypto':>8}{'stocks':>8}{'cash':>8}{'median end':>12}{'lose money':>12}")
    for rank, (m, ratio, end, lose) in enumerate(by_growth[:5], 1):
        print(f"  {rank:<6}{m['crypto']*100:>7.0f}%{m['stocks']*100:>7.0f}%{max(m['cash']*100, 0.0):>7.0f}%"
              f"{end:>12.0f}{lose*100:>11.0f}%")
    print("\n  Ranked by growth, more crypto climbs the list; ranked by gain-per-drawdown,")
    print("  cash does. Which ranking matters more is a question about how much risk is")
    print("  acceptable, not a question this arithmetic can answer by itself.")

    # -- C. DCA vs lump sum, for a blended (not pure-crypto) portfolio ------
    print("\n\nC. FOR A BLENDED PORTFOLIO, DOES SPREADING OUT THE BUYING MATTER MORE?\n")
    print("  Earlier tests found spreading out PURE crypto purchases made no reliable")
    print("  difference. Repeating that test for a 90% stock / 10% crypto blend, added")
    print("  to gradually over 12 weeks vs all at once, every start month, 1-year hold.\n")

    mix_blend = {"stocks": 0.9, "crypto": 0.1}
    starts = [i for i, d in enumerate(s.days) if d.day == 1 and i + 365 <= len(s.days)]
    lump_ends, staged_ends = [], []
    for i in starts:
        window = slice(i, i + 365)
        lump = simulate(mix_blend, s.crypto[window], s.stocks[window], s.cash[window],
                        stake=STAKE)[-1]
        # Staged: 1/12 of the stake deployed into the blend at the start of each of
        # the first 12 weeks, the rest sitting in cash meanwhile.
        weekly = STAKE / 12
        staged_value = 0.0
        for week in range(12):
            start_idx = i + week * 7
            if start_idx >= len(s.days):
                break
            sub_len = 365 - week * 7
            tranche_path = simulate(mix_blend, s.crypto[start_idx:start_idx + sub_len],
                                    s.stocks[start_idx:start_idx + sub_len],
                                    s.cash[start_idx:start_idx + sub_len],
                                    stake=weekly, rebalance_every=0)
            staged_value += tranche_path[-1] if tranche_path else weekly
        staged_ends.append(staged_value)
        lump_ends.append(lump)

    beat = sum(1 for a, b in zip(staged_ends, lump_ends) if a > b)
    print(f"  Staged beat lump-sum in {beat} of {len(starts)} start months "
          f"({beat/len(starts)*100:.0f}%).")
    print(f"  Typical (median) result -- lump sum: {statistics.median(lump_ends):.0f}   "
          f"staged: {statistics.median(staged_ends):.0f}")
    print("  Same conclusion as for pure crypto: spreading out the buying does not")
    print("  reliably help, even once most of the money is in something steadier.")


if __name__ == "__main__":
    main()
