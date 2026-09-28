"""What the same money would have done in a stock market fund or a savings account.

A result like "$237 from $200" means little on its own. Set beside a stock fund
and a savings account tried over the very same starting months, it does. This
follows the conventions of the earlier research (scripts/series.py):

* The fund is the S&P 500 with dividends added back (data/reference/
  spy_adjusted.csv), carried flat over weekends and holidays.
* Savings is the 3-month US Treasury yield (data/reference/tbill_3m.csv),
  accrued daily. Real savings accounts usually pay a little less.

Both are bought or deposited on the same days as the crypto plan (read straight
from the plan's own configuration, so the two cannot drift apart), valued at the
close of the same last day, and charged no trading costs or taxes.

These are statistical yardsticks built from outside data, not ledger entries, so
plain floats are used here (as the research scripts do) and converted to Decimal
only at the boundary. Nothing here touches the price store or the engine.
"""

from __future__ import annotations

import csv
import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from crypto_research.timeutil import DAY_MS, add_months, parse_time, to_iso

from . import plan_builder
from .plan_builder import PlanRequest
from .sweep import SweepResult, WindowOutcome

logger = logging.getLogger(__name__)

FUND_FILE = "spy_adjusted.csv"
RATE_FILE = "tbill_3m.csv"

# A weekend plus a holiday. Data that stops earlier than this before a window
# ends is treated as missing, rather than carried flat and quietly understating
# what happened.
STALE_DAYS_ALLOWED = 4
MIN_ROWS = 60


@dataclass(frozen=True, slots=True)
class ReferenceData:
    """Daily fund prices and savings rates, one entry per calendar day."""

    base_ms: int  # midnight UTC of the first day covered
    fund: tuple[float, ...]  # dividend-adjusted close, carried over non-trading days
    daily_rate: tuple[float, ...]  # savings growth per day: 0.0001 means +0.01%

    def index(self, day_ms: int) -> int:
        return (day_ms - self.base_ms) // DAY_MS

    def covers(self, first_ms: int, last_ms: int) -> bool:
        return self.index(first_ms) >= 0 and self.index(last_ms) < len(self.fund)


@dataclass(frozen=True, slots=True)
class Alternatives:
    fund: tuple[WindowOutcome, ...]
    savings: tuple[WindowOutcome, ...]


@dataclass(frozen=True, slots=True)
class _Flows:
    initial: float  # cash in hand on the first day
    deposits: tuple[tuple[int, float], ...]  # (day_ms, amount) arriving later
    buys: tuple[tuple[int, float], ...]  # (day_ms, amount) spent on the fund


# -- reading the reference files ----------------------------------------------


def build_reference(
    fund_prices: Mapping[int, float], annual_yield_pct: Mapping[int, float]
) -> ReferenceData | None:
    """Daily arrays from {midnight-UTC ms: value} readings, or None if there is too little."""
    if len(fund_prices) < MIN_ROWS or len(annual_yield_pct) < MIN_ROWS:
        return None
    fund_days = sorted(fund_prices)
    rate_days = sorted(annual_yield_pct)
    base = max(fund_days[0], rate_days[0])
    last_real = min(fund_days[-1], rate_days[-1])
    if last_real <= base:
        return None

    fund: list[float] = []
    rates: list[float] = []
    fund_i = rate_i = 0
    current_fund = fund_prices[fund_days[0]]
    current_rate = annual_yield_pct[rate_days[0]]
    for day in range(base, last_real + STALE_DAYS_ALLOWED * DAY_MS + 1, DAY_MS):
        while fund_i < len(fund_days) and fund_days[fund_i] <= day:
            current_fund = fund_prices[fund_days[fund_i]]
            fund_i += 1
        while rate_i < len(rate_days) and rate_days[rate_i] <= day:
            current_rate = annual_yield_pct[rate_days[rate_i]]
            rate_i += 1
        fund.append(current_fund)
        rates.append(current_rate / 100 / 365)
    return ReferenceData(base, tuple(fund), tuple(rates))


def _read_series(path: Path, value_column: str) -> dict[int, float]:
    series: dict[int, float] = {}
    skipped = 0
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                series[parse_time(row["date"])] = float(row[value_column])
            except (KeyError, ValueError, TypeError):
                skipped += 1
    if skipped:
        logger.warning("skipped %d unreadable row(s) in %s", skipped, path)
    return series


def load_reference(directory: Path) -> ReferenceData | None:
    """The reference series, or None when the files are missing or unusable."""
    fund_path, rate_path = directory / FUND_FILE, directory / RATE_FILE
    if not (fund_path.is_file() and rate_path.is_file()):
        logger.info("no stock-fund / savings reference data in %s; comparison hidden", directory)
        return None
    try:
        reference = build_reference(
            _read_series(fund_path, "adjusted_close"), _read_series(rate_path, "annual_yield_pct")
        )
    except OSError:
        logger.exception("could not read the reference data in %s", directory)
        return None
    if reference is None:
        logger.warning("the reference data in %s is too short to use", directory)
    return reference


# -- the comparison ---------------------------------------------------------


def compare(result: SweepResult, reference: ReferenceData) -> Alternatives | None:
    """The stock fund and the savings account over each starting month the plan tried.

    A starting month the reference data does not fully cover is left out of both.
    None when no month is covered at all.
    """
    fund: list[WindowOutcome] = []
    savings: list[WindowOutcome] = []
    for outcome in result.outcomes:
        start_ms = outcome.start_ms
        end_ms = add_months(start_ms, result.hold_months)
        first_ms, last_ms = start_ms, end_ms - DAY_MS  # the plan's last mark is the close of the day before
        if not reference.covers(first_ms, last_ms):
            continue
        flows = _flows_for(result, start_ms, end_ms)
        first, last = reference.index(first_ms), reference.index(last_ms)
        fund_end, fund_fall = _fund_path(flows, reference, first, last)
        savings_end = _savings_end(flows, reference, first, last)
        fund.append(_outcome(start_ms, result.amount, fund_end, fund_fall))
        savings.append(_outcome(start_ms, result.amount, savings_end, 0.0))
    if not fund:
        return None
    return Alternatives(tuple(fund), tuple(savings))


def _outcome(start_ms: int, amount: Decimal, ending: float, fall: float) -> WindowOutcome:
    return WindowOutcome(
        start_ms=start_ms,
        capital_in=amount,
        ending_value=Decimal(repr(round(ending, 4))),
        max_drawdown=Decimal(repr(round(fall, 6))),
        rejected_orders=0,
    )


def _flows_for(result: SweepResult, start_ms: int, end_ms: int) -> _Flows:
    """When the plan's money arrives and is spent, read from the plan's own config."""
    payload = plan_builder.build_config_payload(
        PlanRequest(
            amount=result.amount,
            symbols=result.symbols,
            schedule=result.schedule,
            start=to_iso(start_ms),
            end=to_iso(end_ms),
        )
    )
    strategy = payload["strategy"]
    if strategy["name"] == "benchmark2_contribution_dca":
        deposits = tuple(
            (parse_time(entry["availability_time"]), float(entry["amount"]))
            for entry in payload["portfolio"]["external_contributions"]
        )
        return _Flows(initial=0.0, deposits=deposits, buys=deposits)
    amount = float(payload["portfolio"]["initial_cash"][plan_builder.QUOTE_ASSET])
    tranches = int(strategy["params"]["tranche_count"])
    step = int(strategy["params"]["cadence_days"])
    buys = tuple((start_ms + k * step * DAY_MS, amount / tranches) for k in range(tranches))
    return _Flows(initial=amount, deposits=(), buys=buys)


def _by_day(events: tuple[tuple[int, float], ...], reference: ReferenceData) -> dict[int, float]:
    grouped: dict[int, float] = defaultdict(float)
    for day_ms, amount in events:
        grouped[reference.index(day_ms)] += amount
    return grouped


def _fund_path(
    flows: _Flows, reference: ReferenceData, first: int, last: int
) -> tuple[float, float]:
    """(value on the last day, worst fall from a high) buying the fund on the plan's days.

    The fall is measured per fund-style unit, the way the engine measures the
    plan's own, so a deposit that arrives mid-way cannot hide a drop.
    """
    price = reference.fund
    deposits = _by_day(flows.deposits, reference)
    buys = _by_day(flows.buys, reference)
    cash, units = flows.initial, 0.0
    issued = flows.initial
    peak, worst = 1.0, 0.0
    value = cash
    for day in range(first, last + 1):
        deposit = deposits.get(day, 0.0)
        if deposit:
            before = cash + units * price[max(day - 1, first)]  # priced at the last close, as the engine does
            issued += deposit / (before / issued) if issued > 0 and before > 0 else deposit
            cash += deposit
        spend = min(buys.get(day, 0.0), cash)
        if spend:
            units += spend / price[day]
            cash -= spend
        value = cash + units * price[day]
        if issued > 0:
            nav = value / issued
            peak = max(peak, nav)
            worst = max(worst, (peak - nav) / peak)
    return value, worst


def _savings_end(flows: _Flows, reference: ReferenceData, first: int, last: int) -> float:
    """The balance on the last day: each amount earns the day's rate from the day after it goes in."""
    deposits = _by_day(flows.deposits, reference)
    balance = flows.initial + deposits.get(first, 0.0)
    for day in range(first + 1, last + 1):
        balance = balance * (1 + reference.daily_rate[day]) + deposits.get(day, 0.0)
    return balance
