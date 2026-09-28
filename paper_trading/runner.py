"""One tick of a rehearsal: top up live data, then re-run the backtester.

Every tick does exactly this, for both the rehearsal config and the benchmark
config:

1. fetch whatever new, fully-closed bars Binance.US has published since the
   last tick, into a small database dedicated to this rehearsal;
2. find the newest moment for which *every* configured symbol has a closed
   bar -- never fewer, so a decision is never made on an inconsistent snapshot
   where one coin's price is a day older than another's;
3. if that moment has not advanced since the last tick, stop: nothing new
   happened, and calling ``tick()`` again is a safe no-op;
4. otherwise, run ``crypto_research.experiments.runner.run_experiment`` --
   the same function a backtest calls -- with the config's ``end`` replaced by
   that moment, and record the result the same way a backtest would.

A feed failure (the network, or Binance.US) is caught here and turned into a
logged, FAILED tick. It never reaches ``run_experiment``, so it can never
produce a partial or inconsistent run: a tick either runs both tracks on a
clean, fully-fetched snapshot, or it runs neither.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.config import ExperimentConfig
from crypto_research.datafeed.download import Downloader
from crypto_research.errors import CryptoResearchError, TransportError
from crypto_research.experiments.registry import ExperimentRegistry, RunRecord, config_hash
from crypto_research.experiments.runner import run_experiment
from crypto_research.storage.database import open_database
from crypto_research.storage.repositories import BarRepository
from crypto_research.timeutil import now_ms

from . import ticklog
from .plan import RehearsalPlan, resolved_for_end_time

STATUS_OK = "OK"
STATUS_NO_NEW_BAR = "NO_NEW_BAR"
STATUS_NO_DATA_YET = "NO_DATA_YET"
STATUS_FEED_ERROR = "FEED_ERROR"


@dataclass(slots=True)
class TrackResult:
    role: str  # "rehearsal" | "benchmark"
    run_id: str | None
    status: str | None  # RunRecord.status.value, or None if nothing ran
    reused: bool = False  # True when an existing run for this end_time was found, not re-run


@dataclass(slots=True)
class TickResult:
    status: str
    at: int
    end_time: int | None = None
    rehearsal: TrackResult | None = None
    benchmark: TrackResult | None = None
    detail: str = ""

    @property
    def ran_anything(self) -> bool:
        return self.status == STATUS_OK


def tick(
    plan: RehearsalPlan,
    *,
    client: BinanceUSPublicClient | None = None,
    registry: ExperimentRegistry | None = None,
    now: int | None = None,
    log_path: str | None = None,
) -> TickResult:
    """Run one rehearsal tick. Safe to call as often as you like."""
    client = client or BinanceUSPublicClient()
    registry = registry or ExperimentRegistry(plan.experiment_dir)
    at = now if now is not None else now_ms()
    log_path = log_path or str(ticklog.path_for(plan.experiment_dir, plan.name))

    try:
        end_time = _top_up_and_find_common_end_time(plan, client, at)
    except (TransportError, CryptoResearchError) as exc:
        result = TickResult(status=STATUS_FEED_ERROR, at=at, detail=f"{type(exc).__name__}: {exc}")
        _log(log_path, result)
        return result

    if end_time is None:
        result = TickResult(
            status=STATUS_NO_DATA_YET, at=at,
            detail="no symbol has a fully closed bar yet since the rehearsal's start time",
        )
        _log(log_path, result)
        return result

    resolved_rehearsal = resolved_for_end_time(plan.rehearsal, end_time)
    resolved_benchmark = resolved_for_end_time(plan.benchmark, end_time)

    rehearsal_track = _run_or_reuse("rehearsal", resolved_rehearsal, registry)
    benchmark_track = _run_or_reuse("benchmark", resolved_benchmark, registry)

    status = STATUS_NO_NEW_BAR if (rehearsal_track.reused and benchmark_track.reused) else STATUS_OK
    result = TickResult(
        status=status, at=at, end_time=end_time,
        rehearsal=rehearsal_track, benchmark=benchmark_track,
    )
    _log(log_path, result)
    return result


def latest_completed(registry: ExperimentRegistry, experiment_name: str) -> dict[str, Any] | None:
    """The most recent COMPLETED run recorded under ``experiment_name``, if any."""
    matches = [
        entry for entry in registry.list_runs()
        if entry.get("experiment_name") == experiment_name and entry.get("status") == "COMPLETED"
    ]
    if not matches:
        return None
    newest = max(matches, key=lambda entry: entry["created_at"])
    return registry.load(newest["run_id"])


# --------------------------------------------------------------------------
# internals
# --------------------------------------------------------------------------


def _top_up_and_find_common_end_time(
    plan: RehearsalPlan, client: BinanceUSPublicClient, at: int
) -> int | None:
    """Fetch fresh bars, then the newest moment every symbol has reached, or None."""
    with open_database(plan.db_path) as db:
        Downloader(client, db).backfill_klines(
            list(plan.symbols), plan.interval, start_time=plan.start_time, end_time=at
        )
        bars_repo = BarRepository(db)
        latest_by_symbol: dict[str, int] = {}
        for symbol in plan.symbols:
            bars = bars_repo.load(symbol, plan.interval, start_time=plan.start_time, end_time=at)
            if not bars:
                return None
            latest_by_symbol[symbol] = max(bar.close_time for bar in bars)
    return min(latest_by_symbol.values())


def _run_or_reuse(role: str, config: ExperimentConfig, registry: ExperimentRegistry) -> TrackResult:
    digest = config_hash(config.describe())
    existing = _find_by_digest(registry, digest)
    if existing is not None:
        return TrackResult(role=role, run_id=existing["run_id"], status=existing["status"], reused=True)
    record, _result, _metrics = run_experiment(config, registry=registry)
    return TrackResult(role=role, run_id=record.run_id, status=record.status.value, reused=False)


def _find_by_digest(registry: ExperimentRegistry, digest: str) -> dict[str, Any] | None:
    short = digest[:16]
    for entry in registry.list_runs():
        if entry.get("config_sha256") == short:
            return entry
    return None


def _log(path: str, result: TickResult) -> None:
    ticklog.append(
        path,
        ticklog.TickLogEntry(
            at=result.at,
            status=result.status,
            end_time=result.end_time,
            rehearsal_run_id=result.rehearsal.run_id if result.rehearsal else None,
            rehearsal_status=result.rehearsal.status if result.rehearsal else None,
            benchmark_run_id=result.benchmark.run_id if result.benchmark else None,
            benchmark_status=result.benchmark.status if result.benchmark else None,
            detail=result.detail,
        ),
    )
