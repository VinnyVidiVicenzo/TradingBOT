"""Translate the user's plain-language picks into crypto_research config dicts.

No tkinter import here -- this module is pure and unit-tested on its own.
``crypto_research.config.parse_config`` accepts a plain dict directly (a
public function ``load_config`` itself wraps), so the GUI never writes a
temporary .toml file: it builds a dict here and hands it straight to the
engine.

One PlanRequest describes ONE historical window (a start and an end). The
starting-month view (sweep.py) builds one of these per starting month.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum

from crypto_research.timeutil import DAY_MS, add_months, parse_time, to_iso

# USDT pairs are preferred over USD ones: Binance.US suspended USD trading
# 2023-07-14 to 2025-02-19, so USD-quoted pairs are missing that whole window
# (see README.md). USDT pairs run continuously back to 2020.
QUOTE_ASSET = "USDT"

WEEKLY_TRANCHE_COUNT = 12
MONTHLY_CONTRIBUTION_COUNT = 12
MINIMUM_AMOUNT = Decimal("1")


class Schedule(str, Enum):
    ALL_AT_ONCE = "ALL_AT_ONCE"
    SPREAD_OVER_WEEKS = "SPREAD_OVER_WEEKS"
    ADD_GRADUALLY = "ADD_GRADUALLY"


# Exactly what each radio button says on screen, and the tooltip term used for it.
SCHEDULE_LABELS: dict[Schedule, str] = {
    Schedule.ALL_AT_ONCE: "Put it all in at once",
    Schedule.SPREAD_OVER_WEEKS: "Spread it over 12 weeks",
    Schedule.ADD_GRADUALLY: "Add a little every month",
}

# How each schedule reads inside a sentence: "$200, spread over 12 weeks".
SCHEDULE_PHRASES: dict[Schedule, str] = {
    Schedule.ALL_AT_ONCE: "put in all at once",
    Schedule.SPREAD_OVER_WEEKS: "spread over 12 weeks",
    Schedule.ADD_GRADUALLY: "added a little every month for a year",
}


@dataclass(frozen=True, slots=True)
class PlanRequest:
    amount: Decimal
    symbols: tuple[str, ...]
    schedule: Schedule
    start: str
    end: str
    db_path: str = "data/market.sqlite"


def parse_amount(text: str) -> Decimal:
    """Parse a dollar amount typed by the person, or raise a friendly ValueError."""
    cleaned = text.strip().replace("$", "").replace(",", "")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"{text!r} doesn't look like an amount -- try something like 200") from exc
    if not amount.is_finite():
        raise ValueError(f"{text!r} doesn't look like an amount -- try something like 200")
    if amount < MINIMUM_AMOUNT:
        raise ValueError(f"Enter at least ${MINIMUM_AMOUNT}")
    return amount


def build_config_payload(request: PlanRequest) -> dict:
    """The config for one window: what the person's plan actually does."""
    data = {
        "symbols": list(request.symbols),
        "interval": "1d",
        "start": request.start,
        "end": request.end,
        "db_path": request.db_path,
    }
    if request.schedule is Schedule.ADD_GRADUALLY:
        strategy = {
            "name": "benchmark2_contribution_dca",
            "params": {
                "symbols": list(request.symbols),
                "quote_asset": QUOTE_ASSET,
                "cadence_days": 0,
            },
        }
        portfolio = {
            "valuation_asset": QUOTE_ASSET,
            "external_contributions": _monthly_contributions(request),
        }
    else:
        tranche_count = 1 if request.schedule is Schedule.ALL_AT_ONCE else WEEKLY_TRANCHE_COUNT
        strategy = {
            "name": "benchmark1_staged_deployment",
            "params": {
                "symbols": list(request.symbols),
                "quote_asset": QUOTE_ASSET,
                "tranche_count": tranche_count,
                "cadence_days": 7 if tranche_count > 1 else 0,
            },
        }
        portfolio = {
            "valuation_asset": QUOTE_ASSET,
            "initial_cash": {QUOTE_ASSET: str(request.amount)},
        }
    return {
        "experiment": {"name": "gui-plan-window"},
        "data": data,
        "portfolio": portfolio,
        "strategy": strategy,
    }


def _monthly_contributions(request: PlanRequest) -> list[dict]:
    """12 monthly deposits totalling exactly ``request.amount``.

    Each deposit is rounded to the cent; the last one absorbs the rounding
    remainder so the money put in is exactly what the person typed.

    The first deposit lands one day after ``start``, not on it. The engine
    (crypto_research/backtest/metrics.py) takes "starting value" from the
    first price mark and "contributions" from every deposit, so a deposit
    credited *before* the first mark is counted in both and "money put in"
    comes out too high. Every deposit here falls after the first mark.
    """
    per_month = (request.amount / MONTHLY_CONTRIBUTION_COUNT).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    last = request.amount - per_month * (MONTHLY_CONTRIBUTION_COUNT - 1)
    start_ms = parse_time(request.start)
    contributions = []
    for month_index in range(MONTHLY_CONTRIBUTION_COUNT):
        is_last = month_index == MONTHLY_CONTRIBUTION_COUNT - 1
        contributions.append(
            {
                "amount": str(last if is_last else per_month),
                "asset": QUOTE_ASSET,
                "availability_time": to_iso(add_months(start_ms, month_index) + DAY_MS),
                "contribution_type": "RECURRING_DEPOSIT",
                "label": f"gui-month-{month_index + 1:02d}",
            }
        )
    return contributions
