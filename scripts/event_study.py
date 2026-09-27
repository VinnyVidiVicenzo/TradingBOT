"""Did the big news actually matter?

Takes a calendar of major crypto events -- regulation, collapses, approvals --
and measures what an equal-weight BTC/ETH/SOL basket did around each one. Then
it asks the question that decides whether news is worth acting on: were event
days actually unusual, compared with every other day?

Two honest limits, stated because they bound what this can show:

1. This is HINDSIGHT. Knowing a bill failed on 15 September 2026 is not a
   strategy; you could not have known in August. Anything here that looks like
   an edge is only an edge to someone with the calendar in advance, which is
   nobody.

2. There are about fifteen events. That is far too few to draw statistical
   conclusions about any of them individually. What it CAN do is compare the
   size of event-day moves against the size of ordinary-day moves, which needs
   no statistics at all -- just counting.

Event dates marked VERIFIED were confirmed from sources during this work.
Dates marked RECALLED are from general knowledge and may be off by a day or
two; they are used with wide windows so small errors do not matter.

    python scripts/event_study.py
"""

from __future__ import annotations

import statistics
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from crypto_research.decimals import ZERO, dec, div
from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository
from crypto_research.timeutil import parse_time, to_iso

BASKET = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DATA_START = "2020-10-01"
DATA_END = "2026-09-27"


@dataclass(frozen=True)
class Event:
    date: str
    label: str
    kind: str      # "good" or "bad" as it was understood AT THE TIME
    source: str    # VERIFIED or RECALLED


EVENTS = [
    Event("2021-05-19", "China mining crackdown", "bad", "RECALLED"),
    Event("2021-09-24", "China bans all crypto transactions", "bad", "RECALLED"),
    Event("2022-05-09", "Terra/LUNA collapse begins", "bad", "RECALLED"),
    Event("2022-11-08", "FTX collapse", "bad", "RECALLED"),
    Event("2023-06-05", "SEC sues Binance and Coinbase", "bad", "RECALLED"),
    Event("2024-01-10", "Spot Bitcoin ETFs approved", "good", "RECALLED"),
    Event("2024-04-19", "Bitcoin halving", "good", "RECALLED"),
    Event("2024-11-05", "US presidential election", "good", "RECALLED"),
    Event("2025-03-06", "Strategic Bitcoin Reserve order", "good", "RECALLED"),
    Event("2025-05-29", "CLARITY Act introduced", "good", "VERIFIED"),
    Event("2025-06-17", "GENIUS Act passes Senate 68-30", "good", "VERIFIED"),
    Event("2025-07-17", "CLARITY passes House 294-134", "good", "VERIFIED"),
    Event("2025-07-18", "GENIUS Act signed into law", "good", "VERIFIED"),
    Event("2026-05-14", "CLARITY advanced by Senate cmte 15-9", "good", "VERIFIED"),
    Event("2026-09-15", "CLARITY Act FAILS Senate cloture 49-50", "bad", "VERIFIED"),
]

WINDOWS = (1, 7, 30)


def basket_series(repo: BarRepository) -> tuple[list[int], list[Decimal]]:
    """Equal-weight basket level, rebased to 100, plus its dates."""
    per_symbol = {}
    for symbol in BASKET:
        bars = repo.load(symbol, "1d",
                         start_time=parse_time(DATA_START), end_time=parse_time(DATA_END))
        per_symbol[symbol] = {b.open_time: b.close for b in bars}

    common = sorted(set.intersection(*(set(v) for v in per_symbol.values())))
    level = [dec("100")]
    for earlier, later in zip(common, common[1:]):
        moves = []
        for symbol in BASKET:
            before, after = per_symbol[symbol][earlier], per_symbol[symbol][later]
            if before > 0:
                moves.append(div(after - before, before))
        step = sum(moves, ZERO) / Decimal(len(moves)) if moves else ZERO
        level.append(level[-1] * (Decimal(1) + step))
    return common, level


def pct_change(level: list[Decimal], i: int, j: int) -> float | None:
    if i < 0 or j >= len(level) or level[i] <= 0:
        return None
    return float(div(level[j] - level[i], level[i]) * 100)


def main() -> None:
    with open_database("data/market.sqlite") as db:
        dates, level = basket_series(BarRepository(db))

    index_of = {d: i for i, d in enumerate(dates)}
    daily = [pct_change(level, i - 1, i) for i in range(1, len(level))]
    daily = [d for d in daily if d is not None]

    print("An equal-weight BTC/ETH/SOL basket around major events.")
    print(f"{len(dates)} trading days, {to_iso(dates[0])[:10]} to {to_iso(dates[-1])[:10]}\n")

    print(f"{'date':<12}{'event':<40}{'day':>8}{'next 7d':>10}{'next 30d':>10}{'src':>10}")
    print("-" * 90)
    rows = []
    for event in EVENTS:
        stamp = parse_time(event.date)
        # Snap to the first trading day at or after the event.
        candidates = [d for d in dates if d >= stamp]
        if not candidates:
            continue
        i = index_of[candidates[0]]
        day = pct_change(level, i - 1, i)
        d7 = pct_change(level, i, min(i + 7, len(level) - 1))
        d30 = pct_change(level, i, min(i + 30, len(level) - 1))
        rows.append((event, day, d7, d30))
        fmt = lambda v: "n/a" if v is None else f"{v:+.1f}%"
        print(f"{event.date:<12}{event.label[:39]:<40}{fmt(day):>8}"
              f"{fmt(d7):>10}{fmt(d30):>10}{event.source:>10}")

    print("\n\nWERE EVENT DAYS UNUSUAL? (this needs no statistics, only counting)\n")
    event_moves = [abs(day) for _, day, _, _ in rows if day is not None]
    all_moves = sorted(abs(d) for d in daily)
    typical_event = statistics.median(event_moves)

    def days_bigger_than(threshold: float) -> tuple[int, float]:
        count = sum(1 for m in all_moves if m > threshold)
        return count, count / len(all_moves) * 100

    print(f"  typical day, any day of the year   {statistics.median(all_moves):.1f}%")
    print(f"  typical event day                  {typical_event:.1f}%")
    print(f"  biggest single event-day move      {max(event_moves):.1f}%   "
          f"(the 2021 China crash -- one of the largest ever, so nothing beats it)")
    print()

    # The useful question is not "did anything beat the worst crash in the data",
    # it is "how ordinary is a typical piece of big news".
    count, share = days_bigger_than(typical_event)
    print(f"  Ordinary days that moved more than a TYPICAL event day:")
    print(f"    {count} of {len(all_moves)} days ({share:.0f}%) -- roughly "
          f"{count / 6:.0f} days a year")

    clarity_fail = next(
        (abs(day) for e, day, _, _ in rows if "FAILS" in e.label and day is not None), None
    )
    if clarity_fail is not None:
        count, share = days_bigger_than(clarity_fail)
        print(f"\n  The CLARITY Act failing moved the basket {clarity_fail:.1f}% in a day.")
        print(f"    Ordinary days that moved more: {count} of {len(all_moves)} "
              f"({share:.0f}%) -- about {count / 6:.0f} days a year.")
        print(f"    So the biggest crypto law of the year produced a move this market")
        print(f"    serves up roughly every {len(all_moves) / max(count, 1):.0f} days anyway.")

    print("\n\nDID 'GOOD NEWS' AND 'BAD NEWS' GO THE RIGHT WAY?\n")
    for kind in ("good", "bad"):
        group = [(e, day, d7, d30) for e, day, d7, d30 in rows if e.kind == kind]
        for window, key in zip(WINDOWS, (1, 2, 3)):
            vals = [r[key] for r in group if r[key] is not None]
            right = sum(1 for v in vals if (v > 0) == (kind == "good"))
            label = "up" if kind == "good" else "down"
            print(f"  {kind:<5} news, {window:>2} day(s): went {label} "
                  f"{right} of {len(vals)} times, average {statistics.mean(vals):+.1f}%")
        print()

    print("\nTHE CLARITY ACT, SPECIFICALLY\n")
    clarity = [r for r in rows if "CLARITY" in r[0].label]
    for event, day, d7, d30 in clarity:
        fmt = lambda v: "n/a" if v is None else f"{v:+.1f}%"
        print(f"  {event.date}  {event.label}")
        print(f"    {'on the day':<14}{fmt(day)}")
        print(f"    {'next week':<14}{fmt(d7)}")
        print(f"    {'next month':<14}{fmt(d30)}")


if __name__ == "__main__":
    main()
