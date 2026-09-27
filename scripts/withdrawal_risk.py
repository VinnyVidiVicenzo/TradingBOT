"""Illustration only: if you were withdrawing instead of depositing, would the
ORDER crashes happen in matter?

Not this project's actual situation -- the person is building savings, not
drawing them down -- but requested explicitly, and it is a real, well-known
risk worth understanding even as background knowledge.

The technique: take one real sequence of returns and play it two ways -- once
forward (as it actually happened) and once with the days in reverse order.
With NO withdrawals, forward and reversed compounding land on the exact same
ending value (multiplying the same numbers in a different order gives the same
product). The moment regular WITHDRAWALS enter the picture, that stops being
true: withdrawing a fixed dollar amount during a crash permanently removes
capital that a later recovery can no longer grow, so whichever order puts the
crash EARLY comes out worse. This is "sequence of returns risk," and it is
demonstrated here with real data rather than a made-up example, across every
3-year window available rather than one cherry-picked pair.

    python scripts/withdrawal_risk.py
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from series import build

STAKE = 200.0
MONTHLY_WITHDRAWAL = 5.0   # 2.5% of the stake per month -- deliberately large
                           # enough to matter within a 3-year window
YEARS = 3


def run_with_withdrawals(returns: list[float]) -> float:
    balance = STAKE
    for day, r in enumerate(returns):
        balance *= 1 + r
        if day > 0 and day % 30 == 0:
            balance = max(0.0, balance - MONTHLY_WITHDRAWAL)
    return balance


def main() -> None:
    s = build()
    length = 365 * YEARS
    starts = [i for i, d in enumerate(s.days) if d.day == 1 and i + length <= len(s.days)]

    print(f"${STAKE:.0f} invested in crypto (the same rotating basket used throughout this")
    print(f"project), withdrawing ${MONTHLY_WITHDRAWAL:.0f} a month for {YEARS} years. Same returns,")
    print("just replayed in the opposite order -- crash-first vs crash-last.\n")

    print(f"{'start month':<14}{'forward order':>16}{'reversed order':>16}{'gap':>10}")
    print("-" * 56)
    forward_ends, reversed_ends = [], []
    for i in starts:
        window = s.crypto[i:i + length]
        fwd = run_with_withdrawals(window)
        rev = run_with_withdrawals(list(reversed(window)))
        forward_ends.append(fwd)
        reversed_ends.append(rev)
        gap = fwd - rev
        if abs(gap) > 20:  # only print the windows where it visibly mattered
            print(f"{s.days[i].isoformat()[:7]:<14}{fwd:>16.0f}{rev:>16.0f}{gap:>+10.0f}")

    print(f"\n{len(starts)} windows tested (only large gaps shown above; every window is")
    print("in the totals below).\n")
    print(f"Typical (median) ending balance, forward order:  {statistics.median(forward_ends):.0f}")
    print(f"Typical (median) ending balance, reversed order:  {statistics.median(reversed_ends):.0f}")
    gaps = [f - r for f, r in zip(forward_ends, reversed_ends)]
    print(f"\nMedian gap between the two orderings: {statistics.median(gaps):+.0f}")
    print(f"Largest gap in either direction: {max(gaps, key=abs):+.0f}")
    print(f"Order mattered by more than $20 in {sum(1 for g in gaps if abs(g) > 20)} of "
          f"{len(gaps)} windows.")

    print("\nWhy this matters in plain terms: with no withdrawals at all, the order of")
    print("the SAME returns would make no difference whatsoever to the ending balance --")
    print("multiplying the same numbers in a different sequence gives the same answer.")
    print("Every gap shown above exists ONLY because of the $5/month coming out along the")
    print("way. A crash early in the withdrawal period locks in a loss on money that a")
    print("later recovery can no longer grow, because that money is already gone.")
    print("\nThis project's own situation is the opposite -- money going IN, not out -- and")
    print("earlier tests already showed that when you are contributing rather than")
    print("withdrawing, the timing of a crash matters far less to the end result.")


if __name__ == "__main__":
    main()
