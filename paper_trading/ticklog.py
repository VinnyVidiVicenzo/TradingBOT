"""An append-only record of every tick attempt, whatever it found.

Separate from the experiment registry, which only ever hears about a tick that
reached a run. A day with no new bar, or a day the feed could not be reached,
is not a run -- but it is still a fact worth keeping, because "the rehearsal
went quiet for seven days" is exactly the kind of thing the scorecard and the
weekly report need to say plainly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from crypto_research.jsonl import append_record, read_records


@dataclass(frozen=True, slots=True)
class TickLogEntry:
    at: int
    status: str
    end_time: int | None = None
    rehearsal_run_id: str | None = None
    rehearsal_status: str | None = None
    benchmark_run_id: str | None = None
    benchmark_status: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at,
            "status": self.status,
            "end_time": self.end_time,
            "rehearsal_run_id": self.rehearsal_run_id,
            "rehearsal_status": self.rehearsal_status,
            "benchmark_run_id": self.benchmark_run_id,
            "benchmark_status": self.benchmark_status,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TickLogEntry":
        return cls(
            at=int(payload["at"]),
            status=str(payload["status"]),
            end_time=None if payload.get("end_time") is None else int(payload["end_time"]),
            rehearsal_run_id=payload.get("rehearsal_run_id"),
            rehearsal_status=payload.get("rehearsal_status"),
            benchmark_run_id=payload.get("benchmark_run_id"),
            benchmark_status=payload.get("benchmark_status"),
            detail=str(payload.get("detail", "")),
        )


def path_for(experiment_dir: str, plan_name: str) -> Path:
    if not plan_name or Path(plan_name).name != plan_name or any(c in plan_name for c in '/\\:'):
        raise ValueError("plan name must be a simple filename component")
    return Path(experiment_dir) / f"paper_ticks_{plan_name}.jsonl"


def append(path: str | Path, entry: TickLogEntry) -> None:
    append_record(path, entry.to_dict())


def read_all(path: str | Path) -> list[TickLogEntry]:
    return read_records(path, TickLogEntry.from_dict)
