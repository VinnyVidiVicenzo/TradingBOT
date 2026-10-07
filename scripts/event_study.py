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

Every date below is now VERIFIED against a source; see
results/reference_verification.md for the search-by-search record. Nine dates
were originally entered from memory and marked RECALLED. Checking them found
two genuinely wrong by two days each (not just "might be off") -- corrected
in place, with the old value left in a comment. A one-day Bitcoin-halving
ambiguity was resolved by this project's own UTC convention rather than
picking whichever source was more convenient.

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
    # All nine of the entries below were originally entered from memory and
    # marked RECALLED with a stated +/-1-2 day margin of error. Verified by web
    # search during the independent-review pass; two dates were actually wrong
    # by 2 days each (not just "might be off"), corrected here, all now VERIFIED.
    # Sources: see results/reference_verification.md.
    Event("2021-05-21", "China mining crackdown", "bad", "VERIFIED"),  # was 05-19
    Event("2021-09-24", "China bans all crypto transactions", "bad", "VERIFIED"),
    Event("2022-05-07", "Terra/LUNA collapse begins", "bad", "VERIFIED"),  # was 05-09
    Event("2022-11-08", "FTX collapse", "bad", "VERIFIED"),
    Event("2023-06-05", "SEC sues Binance and Coinbase", "bad", "VERIFIED"),
    Event("2024-01-10", "Spot Bitcoin ETFs approved", "good", "VERIFIED"),
    # Sources disagree by hours around the UTC/US-timezone boundary (block
    # 840,000; commonly cited as "Apr 19 US evening / Apr 20 UTC morning").
    # This project times everything in UTC throughout, so UTC's date wins.
    Event("2024-04-20", "Bitcoin halving", "good", "VERIFIED"),  # was 04-19
    Event("2024-11-05", "US presidential election", "good", "VERIFIED"),
    Event("2025-03-06", "Strategic Bitcoin Reserve order", "good", "VERIFIED"),
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


def forward_change(dates: list[int], level: list[Decimal], i: int, days: int) -> float | None:
    """Require the complete calendar horizon, including its exact endpoint."""
    from bisect import bisect_left
    from crypto_research.timeutil import DAY_MS
    target = dates[i] + days * DAY_MS
    end = bisect_left(dates, target)
    if end >= len(dates) or dates[end] != target:
        return None
    return pct_change(level, i, end)


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
        d7 = forward_change(dates, level, i, 7)
        d30 = forward_change(dates, level, i, 30)
        rows.append((event, day, d7, d30))
        fmt = lambda v: "n/a" if v is None else f"{v:+.1f}%"
        print(f"{event.date:<12}{event.label[:39]:<40}{fmt(day):>8}"
              f"{fmt(d7):>10}{fmt(d30):>10}{event.source:>10}")

    print("\n\nWERE EVENT DAYS UNUSUAL? (this needs no statistics, only counting)\n")
    labelled_moves = [(abs(day), event.label, event.date)
                      for event, day, _, _ in rows if day is not None]
    event_moves = [m for m, _, _ in labelled_moves]
    all_moves = sorted(abs(d) for d in daily)
    typical_event = statistics.median(event_moves)

    def days_bigger_than(threshold: float) -> tuple[int, float]:
        count = sum(1 for m in all_moves if m > threshold)
        return count, count / len(all_moves) * 100

    # Named dynamically, not hard-coded: an earlier version of this line
    # named "the 2021 China crash" unconditionally, written when that really
    # was the biggest move on record. Correcting that event's date (see the
    # module docstring) shrank it below FTX's, and the hard-coded label kept
    # citing the wrong event even after the number changed underneath it.
    biggest_move, biggest_label, biggest_date = max(labelled_moves, key=lambda t: t[0])
    print(f"  typical day, any day of the year   {statistics.median(all_moves):.1f}%")
    print(f"  typical event day                  {typical_event:.1f}%")
    print(f"  biggest single event-day move      {biggest_move:.1f}%   "
          f"({biggest_date}, {biggest_label})")
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
