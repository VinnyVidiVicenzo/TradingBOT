"""Plain-language wording for a sweep: table rows, headline, notes, tooltips.

Pure -- no Tk. Everything a person reads about a result is written here, in
one place, so the tone stays consistent and is easy to review.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from crypto_research.timeutil import parse_time, to_datetime

from . import coin_catalog
from .plan_builder import SCHEDULE_PHRASES
from .sweep import HOLD_LABELS, SweepResult, WindowOutcome
from .sweep_summary import NARROW_SPAN_MONTHS, SweepSummary


@dataclass(frozen=True, slots=True)
class StatRow:
    label: str
    value: str
    term: str | None = None


# Every glossary term a StatRow below can carry. desktop_app/glossary.py must
# define all of these -- tests/test_desktop_app_glossary.py asserts it.
GLOSSARY_TERMS_USED: tuple[str, ...] = (
    "worst starting month",
    "typical result",
    "best starting month",
    "lost money",
    "doubled your money or better",
    "fell by half or more at some point",
    "just keeping it as cash",
)


@dataclass(frozen=True, slots=True)
class Column:
    title: str
    term: str


@dataclass(frozen=True, slots=True)
class Cell:
    text: str
    tooltip: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    label: str
    term: str
    cells: tuple[Cell, ...]


@dataclass(frozen=True, slots=True)
class Comparison:
    columns: tuple[Column, ...]
    rows: tuple[ComparisonRow, ...]


# Every glossary term the comparison table can carry (row labels and column headers).
COMPARISON_TERMS_USED: tuple[str, ...] = (
    "worst starting month",
    "typical result",
    "best starting month",
    "lost money",
    "doubled your money or better",
    "fell by half or more at some point",
    "your plan",
    "stock market fund",
    "savings account",
)


def format_money(value: Decimal, *, cents: bool = True) -> str:
    rounded = value.quantize(Decimal("0.01" if cents else "1"), rounding=ROUND_HALF_UP)
    sign = "-" if rounded < 0 else ""
    body = f"{abs(rounded):,.2f}" if cents else f"{abs(rounded):,.0f}"
    return f"{sign}${body}"


def format_percent(fraction: Decimal, *, places: int = 0) -> str:
    pct = (fraction * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = "-" if pct < 0 else "+"
    return f"{sign}{abs(pct):,.{places}f}%"


def month_label(start_ms: int) -> str:
    return to_datetime(start_ms).strftime("%b %Y")


def when_label(created_at_iso: str) -> str:
    """'Sep 27, 2026, 7:12 PM' -- in the person's own time zone, not UTC."""
    moment = to_datetime(parse_time(created_at_iso)).astimezone()
    hour = moment.hour % 12 or 12
    meridiem = "AM" if moment.hour < 12 else "PM"
    return f"{moment:%b} {moment.day}, {moment.year}, {hour}:{moment.minute:02d} {meridiem}"


def coin_names(symbols: tuple[str, ...]) -> str:
    names = []
    for symbol in symbols:
        base = symbol[: -len(coin_catalog.QUOTE_ASSET)] if symbol.endswith(coin_catalog.QUOTE_ASSET) else symbol
        names.append(coin_catalog.FRIENDLY_NAMES.get(base, base))
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _cents(amount: Decimal) -> bool:
    # Whole dollars read better for a normal stake; small stakes need the cents.
    return amount < 100


def title(result: SweepResult) -> str:
    return (
        f"{format_money(result.amount, cents=_cents(result.amount))} in "
        f"{coin_names(result.symbols)}, {SCHEDULE_PHRASES[result.schedule]}"
    )


def subtitle(result: SweepResult, summary: SweepSummary) -> str:
    return (
        "Tried starting on the 1st of every month from "
        f"{month_label(summary.first_start_ms)} to {month_label(summary.last_start_ms)} "
        f"({summary.tested} starting months), holding each for "
        f"{HOLD_LABELS[result.hold_months]}. How the tries ended:"
    )


def describe(result: SweepResult) -> str:
    """One line for the 'Your past plans' list."""
    return (
        f"{format_money(result.amount, cents=_cents(result.amount))}  |  "
        f"{coin_names(result.symbols)}  |  {SCHEDULE_PHRASES[result.schedule]}  |  "
        f"{HOLD_LABELS[result.hold_months]}"
    )


def rows(summary: SweepSummary) -> list[StatRow]:
    cents = _cents(summary.amount)

    def money(value: Decimal) -> str:
        return format_money(value, cents=cents)

    n = summary.tested
    return [
        StatRow(
            "Worst starting month",
            f"{money(summary.worst.ending_value)}  (started {month_label(summary.worst.start_ms)})",
            "worst starting month",
        ),
        StatRow("Typical result", money(summary.typical_ending), "typical result"),
        StatRow(
            "Best starting month",
            f"{money(summary.best.ending_value)}  (started {month_label(summary.best.start_ms)})",
            "best starting month",
        ),
        StatRow("Lost money", f"{summary.lost_money} of {n} starting months", "lost money"),
        StatRow(
            "Doubled your money or better",
            f"{summary.doubled_or_better} of {n}",
            "doubled your money or better",
        ),
        StatRow(
            "Fell by half or more at some point",
            f"{summary.halved_at_some_point} of {n}",
            "fell by half or more at some point",
        ),
        StatRow(
            "Just keeping it as cash",
            f"{money(summary.amount)} every time",
            "just keeping it as cash",
        ),
    ]


# Each buy is charged the engine's default costs: a 0.6% exchange fee plus 5 basis
# points of price gap and 5 of slippage. tests/test_desktop_app_metrics_presenter.py
# checks this figure against those defaults, so it cannot silently go stale.
ASSUMED_COST_PERCENT = "0.7%"


def comparison(
    plan: SweepSummary, fund: SweepSummary | None, savings: SweepSummary | None
) -> Comparison:
    """The plan side by side with whichever yardsticks could be worked out."""
    columns = [Column("Your plan", "your plan")]
    summaries = [plan]
    if fund is not None:
        columns.append(Column("Stock market fund", "stock market fund"))
        summaries.append(fund)
    if savings is not None:
        columns.append(Column("Savings account", "savings account"))
        summaries.append(savings)

    cents = _cents(plan.amount)

    def money(value: Decimal) -> str:
        return format_money(value, cents=cents)

    def extreme(index: int, outcome: WindowOutcome) -> Cell:
        # Only the plan's own column has room for the month; the yardsticks carry it in a tooltip.
        if index == 0:
            return Cell(f"{money(outcome.ending_value)} ({month_label(outcome.start_ms)})")
        return Cell(money(outcome.ending_value), f"Started {month_label(outcome.start_ms)}")

    def count(value: int, summary: SweepSummary) -> Cell:
        return Cell(f"{value} of {summary.tested}")

    rows = (
        ComparisonRow(
            "Worst starting month", "worst starting month",
            tuple(extreme(i, s.worst) for i, s in enumerate(summaries)),
        ),
        ComparisonRow(
            "Typical result", "typical result",
            tuple(Cell(money(s.typical_ending)) for s in summaries),
        ),
        ComparisonRow(
            "Best starting month", "best starting month",
            tuple(extreme(i, s.best) for i, s in enumerate(summaries)),
        ),
        ComparisonRow(
            "Lost money", "lost money", tuple(count(s.lost_money, s) for s in summaries)
        ),
        ComparisonRow(
            "Doubled or better", "doubled your money or better",
            tuple(count(s.doubled_or_better, s) for s in summaries),
        ),
        ComparisonRow(
            "Fell by half or more", "fell by half or more at some point",
            tuple(count(s.halved_at_some_point, s) for s in summaries),
        ),
    )
    return Comparison(tuple(columns), rows)


def notes(
    summary: SweepSummary, *, compared: bool = False, compared_months: int | None = None
) -> list[str]:
    lines = [
        "The result depended far more on when you started than on the plan itself. "
        "These are coins that still exist today; coins that disappeared aren't in "
        "the data, so real results would tend to be somewhat worse. Past results "
        "don't predict future ones.",
        "Amounts are in USDT, a coin designed to stay at one US dollar. Each buy "
        f"includes assumed trading costs of about {ASSUMED_COST_PERCENT} (an exchange "
        "fee plus small price gaps). Taxes and savings-account interest are not counted.",
    ]
    if compared:
        lines.append(
            "The stock fund and savings account are tried over the same starting months "
            "and bought on the same days as your plan, with no trading costs or taxes. "
            "2020-26 had no major stock market crash (stocks fell by about half in "
            "2000-02 and again in 2008-09), so the fund looks steadier here than it can be."
        )
        if compared_months is not None and compared_months < summary.tested:
            lines.append(
                f"The stock fund and savings account cover {compared_months} of the "
                f"{summary.tested} starting months, because their saved history doesn't "
                "reach the rest."
            )
    if summary.span_months < NARROW_SPAN_MONTHS:
        lines.append(
            f"Only starting months from {month_label(summary.first_start_ms)} to "
            f"{month_label(summary.last_start_ms)} could be tried for these coins. That is a "
            "narrow slice of history, so it says less about how much timing matters than "
            "a longer record would."
        )
    if summary.skipped:
        lines.append(
            f"{summary.skipped} starting month(s) weren't tried because a chosen coin "
            "had no saved prices then."
        )
    if summary.windows_with_rejections:
        lines.append(
            f"In {summary.windows_with_rejections} of {summary.tested} tries, some buys were "
            "too small for the exchange to accept, so part of the money was never "
            "invested. A larger amount or fewer coins avoids this."
        )
    return lines


def cell_tooltip(outcome: WindowOutcome) -> str:
    """What hovering one starting month on the map says."""
    cents = _cents(outcome.capital_in)
    change = (
        (outcome.ending_value - outcome.capital_in) / outcome.capital_in
        if outcome.capital_in > 0
        else Decimal(0)
    )
    return (
        f"Started {month_label(outcome.start_ms)}: "
        f"{format_money(outcome.capital_in, cents=cents)} ended at "
        f"{format_money(outcome.ending_value, cents=cents)} ({format_percent(change)})"
    )
