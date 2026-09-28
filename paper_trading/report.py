"""A plain-language write-up of one rehearsal, so far.

Deliberately descriptive. It states what happened to the pretend money under
the plan and under the benchmark, side by side, and it never claims the
comparison shows which one is better: a rehearsal of a few weeks is a
mechanical check, not proof of anything about the future. See
docs/trading-bot/PAPER_TRADING.md.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from crypto_research.decimals import dec
from crypto_research.timeutil import to_iso

from .scorecard import Scorecard
from .ticklog import TickLogEntry

NO_EDGE_REMINDER = (
    "A rehearsal this short cannot show that the plan beats the benchmark, or that "
    "it does not. It shows whether the plan runs the way it is supposed to, and it "
    "starts collecting real prices and real order-book costs for later, longer "
    "research. Do not treat the numbers below as a verdict."
)


def build_report(
    plan_name: str,
    *,
    rehearsal_record: Mapping[str, Any] | None,
    benchmark_record: Mapping[str, Any] | None,
    scorecard: Scorecard,
    ticks: Sequence[TickLogEntry],
) -> str:
    lines = [f"Rehearsal report: {plan_name}", "=" * (18 + len(plan_name)), ""]

    if not ticks:
        lines.append("No ticks have run yet.")
        return "\n".join(lines)

    lines.append(f"Ticks so far      : {len(ticks)}")
    lines.append(f"First tick        : {to_iso(ticks[0].at)}")
    lines.append(f"Latest tick       : {to_iso(ticks[-1].at)} ({ticks[-1].status})")
    status_counts: dict[str, int] = {}
    for entry in ticks:
        status_counts[entry.status] = status_counts.get(entry.status, 0) + 1
    lines.append("Tick outcomes     : " + ", ".join(f"{s}={n}" for s, n in sorted(status_counts.items())))
    lines.append("")

    if rehearsal_record is None and benchmark_record is None:
        lines.append("Neither track has completed a run yet -- there is no new price data to act on.")
        lines.append("")
        lines.append(NO_EDGE_REMINDER)
        return "\n".join(lines)

    lines.append(_comparison_table(rehearsal_record, benchmark_record))
    lines.append("")
    lines.append("Scorecard (mechanical checks, not a performance grade):")
    for check in scorecard.checks:
        lines.append(f"  [{check.result:<17}] {check.id}: {check.detail}")
    lines.append("")
    lines.append(NO_EDGE_REMINDER)
    return "\n".join(lines)


def _comparison_table(
    rehearsal: Mapping[str, Any] | None, benchmark: Mapping[str, Any] | None
) -> str:
    columns: list[tuple[str, Mapping[str, Any]]] = []
    if rehearsal is not None:
        columns.append(("rehearsal", rehearsal))
    if benchmark is not None:
        columns.append(("benchmark", benchmark))

    rows: list[tuple[str, Any]] = [
        ("as of", lambda r: to_iso(int(r["date_range"]["end_time"]))[:10]),
        ("capital in", lambda r: _money(r["metrics"]["total_capital_in"])),
        ("value now", lambda r: _money(r["metrics"]["ending_portfolio_value"])),
        ("simple return", lambda r: _pct(r["metrics"]["net_return"])),
        ("worst drop so far", lambda r: _pct(r["metrics"]["max_drawdown"])),
        ("cash left over", lambda r: _money(r["metrics"]["uninvested_cash_value"])),
        ("fees + spread + slippage", lambda r: _money(r["metrics"]["total_cost_drag"])),
        ("orders ok / rejected", lambda r: f"{r['metrics']['accepted_orders']} / {r['metrics']['rejected_orders']}"),
    ]
    label_width = max((len(label) for label, _ in rows), default=0) + 2
    cells: list[list[str]] = []
    for _, getter in rows:
        row_cells = []
        for _, record in columns:
            try:
                row_cells.append(str(getter(record)))
            except (KeyError, TypeError):
                row_cells.append("-")
        cells.append(row_cells)
    column_width = max(
        [len(name) for name, _ in columns] + [len(cell) for row in cells for cell in row] + [10]
    )
    header = "".ljust(label_width) + "".join(name.rjust(column_width + 2) for name, _ in columns)
    lines = [header, "-" * len(header)]
    for (label, _), row_cells in zip(rows, cells):
        line = label.ljust(label_width)
        for cell in row_cells:
            line += cell.rjust(column_width + 2)
        lines.append(line)
    return "\n".join(lines)


def _money(value: str) -> str:
    return f"${dec(value):,.2f}"


def _pct(value: str) -> str:
    return f"{dec(value) * 100:.2f}%"
