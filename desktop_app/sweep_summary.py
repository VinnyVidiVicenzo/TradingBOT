"""Turn a SweepResult into the few numbers a person can read at a glance.

Pure -- no engine, no Tk. The table counts and the map colours both come from
``bucket_for``, so the two can never disagree about what "lost money" means.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Sequence

from crypto_research.timeutil import to_datetime

from .sweep import SweepResult, WindowOutcome

_HALF = Decimal("0.5")
_DOUBLE = Decimal(2)

# Starting months spread over less than this many months are a narrow slice of history.
NARROW_SPAN_MONTHS = 24


class Bucket(str, Enum):
    LOST_MOST = "LOST_MOST"  # ended below half of what was put in
    LOST_SOME = "LOST_SOME"  # ended below what was put in, but not by half
    GAINED = "GAINED"  # ended at or above what was put in
    DOUBLED = "DOUBLED"  # ended at double what was put in, or more


def bucket_for(outcome: WindowOutcome) -> Bucket:
    if outcome.capital_in <= 0:  # nothing was put in, so nothing was gained or lost
        return Bucket.GAINED
    ratio = outcome.ending_value / outcome.capital_in
    if ratio >= _DOUBLE:
        return Bucket.DOUBLED
    if ratio >= 1:
        return Bucket.GAINED
    if ratio >= _HALF:
        return Bucket.LOST_SOME
    return Bucket.LOST_MOST


def invested_nothing(result: SweepResult) -> bool:
    """Was every buy in every try refused, so that no money ever left cash?

    (Every try ended exactly where it began, and each had refused buys.) The
    result would then be a wall of "no change", which reads as good news.
    """
    return bool(result.outcomes) and all(
        outcome.rejected_orders > 0 and outcome.ending_value == outcome.capital_in
        for outcome in result.outcomes
    )


@dataclass(frozen=True, slots=True)
class SweepSummary:
    amount: Decimal
    tested: int
    skipped: int
    first_start_ms: int
    last_start_ms: int
    # Calendar months between the first and last starting month tried.
    span_months: int
    worst: WindowOutcome
    best: WindowOutcome
    typical_ending: Decimal
    lost_money: int
    doubled_or_better: int
    halved_at_some_point: int
    windows_with_rejections: int


def summarize(result: SweepResult) -> SweepSummary | None:
    """None when there is nothing to summarise (no starting month could be run)."""
    return summarize_outcomes(result.outcomes, amount=result.amount, skipped=result.skipped)


def summarize_outcomes(
    outcomes: Sequence[WindowOutcome], *, amount: Decimal, skipped: int = 0
) -> SweepSummary | None:
    """The same summary for any set of starting-month results -- the plan's, or a yardstick's."""
    if not outcomes:
        return None
    endings = sorted(outcome.ending_value for outcome in outcomes)
    middle = len(endings) // 2
    typical = endings[middle] if len(endings) % 2 else (endings[middle - 1] + endings[middle]) / 2
    starts = [outcome.start_ms for outcome in outcomes]
    buckets = [bucket_for(outcome) for outcome in outcomes]
    first, last = to_datetime(min(starts)), to_datetime(max(starts))
    return SweepSummary(
        amount=amount,
        tested=len(outcomes),
        skipped=skipped,
        first_start_ms=min(starts),
        last_start_ms=max(starts),
        span_months=(last.year - first.year) * 12 + (last.month - first.month),
        worst=min(outcomes, key=lambda o: (o.ending_value, o.start_ms)),
        best=max(outcomes, key=lambda o: (o.ending_value, -o.start_ms)),
        typical_ending=typical,
        lost_money=sum(b in (Bucket.LOST_SOME, Bucket.LOST_MOST) for b in buckets),
        doubled_or_better=sum(b is Bucket.DOUBLED for b in buckets),
        halved_at_some_point=sum(o.max_drawdown >= _HALF for o in outcomes),
        windows_with_rejections=sum(o.rejected_orders > 0 for o in outcomes),
    )
