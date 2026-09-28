"""A one-line-per-plan history for the 'Your past plans' screen.

experiments/ (the research log) is append-only and holds one record per
*experiment*. A starting-month view is dozens of exploratory runs, so it is
not written there; instead each finished sweep is appended, as one JSON line,
to a small file of its own. Lines are only ever appended, never rewritten.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .plan_builder import Schedule
from .sweep import HOLD_LABELS, SweepResult, WindowOutcome

logger = logging.getLogger(__name__)

FILENAME = "gui_plans.jsonl"
_VERSION = 1


@dataclass(frozen=True, slots=True)
class StoredPlan:
    created_at_iso: str
    result: SweepResult


def append(path: Path, result: SweepResult, *, created_at_iso: str) -> None:
    record = {
        "version": _VERSION,
        "created_at": created_at_iso,
        "amount": str(result.amount),
        "symbols": list(result.symbols),
        "schedule": result.schedule.value,
        "hold_months": result.hold_months,
        "skipped": result.skipped,
        "outcomes": [
            {
                "start_ms": outcome.start_ms,
                "capital_in": str(outcome.capital_in),
                "ending_value": str(outcome.ending_value),
                "max_drawdown": str(outcome.max_drawdown),
                "rejected_orders": outcome.rejected_orders,
            }
            for outcome in result.outcomes
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    if _ends_mid_line(path):
        # A crash cut the previous write short. Without this the new plan would be
        # glued onto the damaged line and lost along with it.
        line = "\n" + line
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def _ends_mid_line(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as handle:
        handle.seek(-1, 2)
        return handle.read(1) != b"\n"


def load_all(path: Path) -> list[StoredPlan]:
    """Every readable plan, oldest first. A damaged line is skipped, not fatal."""
    if not path.exists():
        return []
    plans: list[StoredPlan] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                plans.append(_from_record(json.loads(text)))
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                # A line cut short by a crash, or edited by hand.
                logger.warning("skipping unreadable line %d of %s: %s", number, path, exc)
    return plans


def _from_record(record: Mapping[str, Any]) -> StoredPlan:
    if record["version"] != _VERSION:
        raise ValueError(f"unknown history format version {record['version']!r}")
    hold_months = int(record["hold_months"])
    if hold_months not in HOLD_LABELS:
        raise ValueError(f"unsupported hold length {hold_months}")
    outcomes = tuple(
        WindowOutcome(
            start_ms=int(item["start_ms"]),
            capital_in=Decimal(item["capital_in"]),
            ending_value=Decimal(item["ending_value"]),
            max_drawdown=Decimal(item["max_drawdown"]),
            rejected_orders=int(item["rejected_orders"]),
        )
        for item in record["outcomes"]
    )
    result = SweepResult(
        amount=Decimal(record["amount"]),
        symbols=tuple(str(symbol) for symbol in record["symbols"]),
        schedule=Schedule(record["schedule"]),
        hold_months=hold_months,
        outcomes=outcomes,
        skipped=int(record["skipped"]),
    )
    return StoredPlan(created_at_iso=str(record["created_at"]), result=result)
