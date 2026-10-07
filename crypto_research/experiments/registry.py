"""Append-only experiment registry.

Two rules:

* **Nothing is overwritten.** A run directory that already exists is never
  reused. Re-running the same config produces a new run ID, so the two can be
  compared instead of one replacing the other.

* **Failures are kept.** A run that raised is recorded with status FAILED and
  its traceback. Discarding failed runs is how a research log quietly turns
  into a record of only the results that happened to work.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from ..timeutil import now_ms, to_iso

INDEX_FILE = "index.jsonl"
RUNS_DIR = "runs"


class RunStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(slots=True)
class RunRecord:
    """Everything needed to understand, trust or reproduce one run."""

    run_id: str
    created_at: int
    status: RunStatus
    experiment_name: str
    description: str
    strategy_name: str
    strategy_version: str
    strategy_params: Mapping[str, Any]
    config: Mapping[str, Any]
    config_sha256: str
    dataset_version: Mapping[str, Any] | None
    eligible_symbols: Sequence[str]
    starting_holdings: Sequence[Mapping[str, Any]]
    starting_cash: Mapping[str, str]
    external_contributions: Sequence[Mapping[str, Any]]
    fee_assumptions: Mapping[str, Any]
    spread_assumptions: Mapping[str, Any]
    slippage_assumptions: Mapping[str, Any]
    latency_assumptions: Mapping[str, Any]
    execution_settings: Mapping[str, Any]
    rate_assumptions_used: Sequence[str]
    date_range: Mapping[str, Any]
    metrics: Mapping[str, Any] | None = None
    git_commit: str | None = None
    git_dirty: bool | None = None
    code_version: str = ""
    warnings: Sequence[str] = ()
    error: str | None = None
    traceback: str | None = None
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["created_at_iso"] = to_iso(self.created_at)
        return payload

    def index_entry(self) -> dict[str, Any]:
        """The one-line summary written to index.jsonl."""
        metrics = self.metrics or {}
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "created_at_iso": to_iso(self.created_at),
            "status": self.status.value,
            "experiment_name": self.experiment_name,
            "strategy": f"{self.strategy_name}@{self.strategy_version}",
            "config_sha256": self.config_sha256[:16],
            "dataset_sha256": (self.dataset_version or {}).get("sha256", "")[:16],
            "git_commit": (self.git_commit or "")[:12],
            "git_dirty": self.git_dirty,
            "symbols": list(self.eligible_symbols),
            "net_return": metrics.get("net_return"),
            "ending_portfolio_value": metrics.get("ending_portfolio_value"),
            "max_drawdown": metrics.get("max_drawdown"),
            "accepted_orders": metrics.get("accepted_orders"),
            "rejected_orders": metrics.get("rejected_orders"),
            "error": self.error,
        }


class ExperimentRegistry:
    """On-disk registry rooted at ``experiments/``."""

    def __init__(self, root: str | Path = "experiments") -> None:
        self.root = Path(root)
        self.runs_dir = self.root / RUNS_DIR
        self.index_path = self.root / INDEX_FILE
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # -- ids ---------------------------------------------------------------

    def allocate_run_id(self, config_sha256: str, *, at: int | None = None) -> str:
        """A fresh, human-sortable run ID that does not collide."""
        stamp = to_iso(at if at is not None else now_ms())
        stamp = stamp.replace("-", "").replace(":", "").replace("Z", "").replace(".", "")[:15]
        base = f"{stamp}-{config_sha256[:8]}"
        candidate = base
        suffix = 1
        while (self.runs_dir / candidate).exists():
            candidate = f"{base}-{suffix:02d}"
            suffix += 1
        return candidate

    # -- writing -----------------------------------------------------------

    def write(
        self,
        record: RunRecord,
        *,
        equity_curve_rows: Sequence[Mapping[str, Any]] = (),
        order_rows: Sequence[Mapping[str, Any]] = (),
        cash_ledger_rows: Sequence[Mapping[str, Any]] = (),
    ) -> Path:
        run_dir = self.runs_dir / record.run_id
        if run_dir.exists():
            raise FileExistsError(
                f"run directory {run_dir} already exists; the registry never "
                "overwrites a previous run"
            )
        run_dir.mkdir(parents=True)

        _write_json(run_dir / "record.json", record.to_dict())
        if record.metrics is not None:
            _write_json(run_dir / "metrics.json", record.metrics)
        _write_json(run_dir / "config.resolved.json", record.config)
        if equity_curve_rows:
            _write_csv(run_dir / "equity_curve.csv", equity_curve_rows)
        if order_rows:
            _write_jsonl(run_dir / "orders.jsonl", order_rows)
        if cash_ledger_rows:
            _write_csv(run_dir / "cash_ledger.csv", cash_ledger_rows)

        with self.index_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record.index_entry(), sort_keys=True) + "\n")
        return run_dir

    # -- reading -----------------------------------------------------------

    def list_runs(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        entries = []
        with self.index_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.runs_dir / run_id / "record.json"
        if not path.exists():
            raise FileNotFoundError(f"no run {run_id!r} in {self.runs_dir}")
        return json.loads(path.read_text(encoding="utf-8"))

    def iter_runs(self) -> Iterator[dict[str, Any]]:
        for directory in sorted(self.runs_dir.iterdir()):
            record = directory / "record.json"
            if record.exists():
                yield json.loads(record.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def config_hash(payload: Mapping[str, Any]) -> str:
    """Stable hash of a resolved configuration."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def git_state(cwd: str | Path = ".") -> tuple[str | None, bool | None]:
    """(commit sha, working tree dirty). ``(None, None)`` outside a repo.

    Recorded because a result produced from uncommitted code is not
    reproducible, and the record should say so rather than imply otherwise.
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if commit.returncode != 0:
            return (None, None)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return (commit.stdout.strip(), dirty)
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return (None, None)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    import csv

    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        text_fields = {"reason", "tag", "client_order_id", "note", "symbol", "asset", "source"}
        writer.writerows({key: _csv_text(value) if key in text_fields else value
                         for key, value in row.items()} for row in rows)


def _csv_text(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.lstrip()
        if (stripped.startswith(("=", "+", "-", "@"))
                or (value and ord(value[0]) < 32)):
            return "'" + value
    return value


def default_experiment_dir() -> str:
    return os.environ.get("CRYPTO_RESEARCH_EXPERIMENT_DIR", "experiments")
