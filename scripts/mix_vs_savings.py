"""Studies 1 and 2: does a small slice of crypto help, and does anything beat savings?

Every mix gets $200 on the same day, is rebalanced back to its target once a
year, and is held for the same length of time, from every month start the data
allows. Mixes and horizons were declared before running (see series.MIXES).

    python scripts/mix_vs_savings.py
"""

from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from series import MIXES, build, pain, simulate

HORIZONS_YEARS = (1, 2, 3, 5)
STAKE = 200.0


def main() -> None:
    s = build()
    rows = []
    for years in HORIZONS_YEARS:
        length = 365 * years
        starts = [i for i, d in enumerate(s.days) if d.day == 1 and i + length <= len(s.days)]
        for i in starts:
            window = slice(i, i + length)
            savings_end = simulate(MIXES["Savings account only"], s.crypto[window],
                                   s.stocks[window], s.cash[window])[-1]
            for name, mix in MIXES.items():
                values = simulate(mix, s.crypto[window], s.stocks[window], s.cash[window])
                facts = pain(values)
                facts.update(years=years, start=s.days[i].isoformat(), mix=name,
                             beat_savings=facts["end"] > savings_end + 0.005)
                rows.append(facts)

    out = Path(__file__).parent.parent / "results/mix_vs_savings.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    for years in HORIZONS_YEARS:
        group = [r for r in rows if r["years"] == years]
        n = len({r["start"] for r in group})
        print(f"\n=== HELD {years} YEAR{'S' if years > 1 else ''}  ({n} start months) ===")
        print(f"{'mix':<26}{'typical':>9}{'average':>9}{'lost $':>9}"
              f"{'beat savings':>14}{'worst drop':>12}{'days in red':>13}")
        print("-" * 92)
        for name in MIXES:
            g = [r for r in group if r["mix"] == name]
            ends = sorted(r["end"] for r in g)
            lost = sum(1 for r in g if r["end"] < STAKE)
            beat = sum(1 for r in g if r["beat_savings"])
            beat_txt = "--" if name == "Savings account only" else f"{beat}/{len(g)}"
            print(f"{name:<26}{ends[len(ends)//2]:>9.0f}{statistics.mean(ends):>9.0f}"
                  f"{f'{lost}/{len(g)}':>9}{beat_txt:>14}"
                  f"{f'-{max(r['worst_drop'] for r in g)*100:.0f}%':>12}"
                  f"{f'{statistics.median(r['share_in_red'] for r in g)*100:.0f}%':>13}")
    print(f"\nall {len(rows)} results written to {out}")


if __name__ == "__main__":
    main()
