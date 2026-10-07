"""Pass/fail rules for a rehearsal, decided before anyone looks at the results.

None of these checks ask "did the plan make money" or "did it beat the
benchmark". A few weeks cannot answer either question -- see
docs/trading-bot/PAPER_TRADING.md -- and grading a rehearsal on performance
would quietly turn "watch it run cleanly" into "declare it a winner the moment
it gets lucky". What these check instead is whether the rehearsal is running
the way it is supposed to: the feed keeps working, nothing crashes, nothing
sells, the books balance, the benchmark is always there to compare against, and
the plan is not so oddly shaped for its coins that most of its orders bounce.

Each check is PASS, FAIL, or NOT_YET_EVALUATED (too little history to judge).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from crypto_research.decimals import dec

from .runner import STATUS_FEED_ERROR, STATUS_NO_NEW_BAR
from .ticklog import TickLogEntry

MIN_TICKS_TO_JUDGE_FEED = 5
MAX_FEED_ERROR_FRACTION = Decimal("0.2")
MAX_REJECTION_FRACTION = Decimal("0.5")


@dataclass(frozen=True, slots=True)
class ScorecardCheck:
    id: str
    result: str  # "PASS" | "FAIL" | "NOT_YET_EVALUATED"
    detail: str


@dataclass(frozen=True, slots=True)
class Scorecard:
    checks: tuple[ScorecardCheck, ...]

    @property
    def fully_passing(self) -> bool:
        """True only once every check is PASS. A pending check counts against this, not for it."""
        return all(c.result == "PASS" for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fully_passing": self.fully_passing,
            "checks": [{"id": c.id, "result": c.result, "detail": c.detail} for c in self.checks],
        }


def evaluate(
    ticks: Sequence[TickLogEntry],
    *,
    rehearsal_record: Mapping[str, Any] | None,
    benchmark_record: Mapping[str, Any] | None,
) -> Scorecard:
    checks = [
        _feed_reliable(ticks),
        _no_crashes(ticks),
        _only_buys(rehearsal_record, benchmark_record),
        _cash_never_negative(rehearsal_record, benchmark_record),
        _benchmark_available(rehearsal_record, benchmark_record),
        _rejection_rate_is_low(rehearsal_record),
    ]
    return Scorecard(tuple(checks))


# --------------------------------------------------------------------------
# individual checks
# --------------------------------------------------------------------------


def _feed_reliable(ticks: Sequence[TickLogEntry]) -> ScorecardCheck:
    ticks = [t for t in ticks if t.status != STATUS_NO_NEW_BAR]
    if len(ticks) < MIN_TICKS_TO_JUDGE_FEED:
        return ScorecardCheck(
            "feed_reliable", "NOT_YET_EVALUATED",
            f"only {len(ticks)} tick(s) so far; need at least {MIN_TICKS_TO_JUDGE_FEED}",
        )
    failures = sum(1 for t in ticks if t.status == STATUS_FEED_ERROR)
    fraction = Decimal(failures) / Decimal(len(ticks))
    passed = fraction <= MAX_FEED_ERROR_FRACTION
    return ScorecardCheck(
        "feed_reliable", "PASS" if passed else "FAIL",
        f"{failures} of {len(ticks)} tick(s) could not reach Binance.US "
        f"({fraction * 100:.0f}%, limit {MAX_FEED_ERROR_FRACTION * 100:.0f}%)",
    )


def _no_crashes(ticks: Sequence[TickLogEntry]) -> ScorecardCheck:
    failed = [
        t for t in ticks
        if t.rehearsal_status == "FAILED" or t.benchmark_status == "FAILED"
    ]
    if not failed:
        return ScorecardCheck("no_crashes", "PASS", "no failed run in the recorded ticks; unlogged crashes are not checked")
    return ScorecardCheck(
        "no_crashes", "FAIL",
        f"{len(failed)} tick(s) recorded a failed run; see the experiment "
        "record's error field for why",
    )


def _only_buys(rehearsal: Mapping[str, Any] | None, benchmark: Mapping[str, Any] | None) -> ScorecardCheck:
    records = [r for r in (rehearsal, benchmark) if r is not None]
    if not records:
        return ScorecardCheck("only_buys", "NOT_YET_EVALUATED", "no completed run yet")
    sold = {r["experiment_name"]: dec(r["metrics"]["total_sold_capital"]) for r in records}
    offenders = {name: amount for name, amount in sold.items() if amount != 0}
    if not offenders:
        return ScorecardCheck("only_buys", "PASS", "no sales in the supplied completed runs")
    return ScorecardCheck(
        "only_buys", "FAIL",
        "a run recorded a sale, which this project never permits: " + ", ".join(
            f"{name}={amount}" for name, amount in offenders.items()
        ),
    )


def _cash_never_negative(rehearsal: Mapping[str, Any] | None, benchmark: Mapping[str, Any] | None) -> ScorecardCheck:
    records = [r for r in (rehearsal, benchmark) if r is not None]
    if not records:
        return ScorecardCheck("cash_never_negative", "NOT_YET_EVALUATED", "no completed run yet")
    negative = {
        r["experiment_name"]: dec(r["metrics"]["uninvested_cash_value"])
        for r in records
        if dec(r["metrics"]["uninvested_cash_value"]) < 0
    }
    if not negative:
        return ScorecardCheck("cash_never_negative", "PASS", "ending aggregate cash is nonnegative in the supplied runs; intermediate buckets are not checked")
    return ScorecardCheck(
        "cash_never_negative", "FAIL",
        "a run ended with negative cash, which should be structurally impossible: "
        + ", ".join(f"{name}={amount}" for name, amount in negative.items()),
    )


def _benchmark_available(rehearsal: Mapping[str, Any] | None, benchmark: Mapping[str, Any] | None) -> ScorecardCheck:
    if rehearsal is None:
        return ScorecardCheck("benchmark_available", "NOT_YET_EVALUATED", "the rehearsal has not completed a run yet")
    if benchmark is not None:
        if rehearsal.get("date_range") != benchmark.get("date_range") or rehearsal.get("dataset_version") != benchmark.get("dataset_version"):
            return ScorecardCheck("benchmark_available", "FAIL", "benchmark horizon or dataset differs from rehearsal")
        return ScorecardCheck("benchmark_available", "PASS", "a benchmark run exists alongside the rehearsal")
    return ScorecardCheck(
        "benchmark_available", "FAIL",
        "the rehearsal has a completed run but the benchmark does not, so nothing can be compared",
    )


def _rejection_rate_is_low(rehearsal: Mapping[str, Any] | None) -> ScorecardCheck:
    if rehearsal is None:
        return ScorecardCheck("rejection_rate_is_low", "NOT_YET_EVALUATED", "no completed run yet")
    metrics = rehearsal["metrics"]
    attempted = int(metrics["attempted_orders"])
    if attempted == 0:
        return ScorecardCheck("rejection_rate_is_low", "NOT_YET_EVALUATED", "no order has been attempted yet")
    rejected = int(metrics["rejected_orders"])
    fraction = Decimal(rejected) / Decimal(attempted)
    passed = fraction <= MAX_REJECTION_FRACTION
    reasons = metrics.get("rejections_by_reason") or {}
    detail = (
        f"{rejected} of {attempted} attempted order(s) were rejected "
        f"({fraction * 100:.0f}%, limit {MAX_REJECTION_FRACTION * 100:.0f}%)"
    )
    if reasons:
        detail += "; reasons: " + ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))
    return ScorecardCheck("rejection_rate_is_low", "PASS" if passed else "FAIL", detail)
