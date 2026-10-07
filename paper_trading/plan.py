"""A rehearsal plan: two ordinary backtest configs, watched side by side.

A rehearsal is not a new kind of configuration. It is exactly the config format
already used for a backtest (see docs/trading-bot/BACKTESTING.md) -- one file
for the plan under test, one for the benchmark it is compared against -- with
two rules layered on top:

* both configs must read the same symbols, at the same interval, from the same
  local database, so the two tracks see byte-identical prices and costs are
  charged the same way to both;
* the ``end`` date in each file is a placeholder that a real backtest would use
  as-is. A rehearsal ignores it: every tick (see ``runner.py``) resolves a fresh
  copy of each config with ``end`` replaced by the newest fully closed bar, so
  re-running the same two files today, tomorrow and next week walks the plan
  forward through real, live prices without editing anything.

Nothing here is a new execution path. A resolved config is run by the exact
``run_experiment`` function a backtest calls (see ``runner.py``), so a
rehearsal's numbers come from code this project has already tested, not from a
copy of it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from crypto_research.config import ExperimentConfig, load_config

from .errors import PlanMismatchError


@dataclass(frozen=True, slots=True)
class RehearsalPlan:
    """The rehearsal track and the benchmark track, verified to share their data."""

    name: str
    rehearsal: ExperimentConfig
    benchmark: ExperimentConfig

    @property
    def db_path(self) -> str:
        return self.rehearsal.data.db_path

    @property
    def symbols(self) -> tuple[str, ...]:
        return self.rehearsal.data.symbols

    @property
    def interval(self) -> str:
        return self.rehearsal.data.interval

    @property
    def start_time(self) -> int:
        return self.rehearsal.data.start_time

    @property
    def experiment_dir(self) -> str:
        return self.rehearsal.experiment_dir

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "symbols": list(self.symbols),
            "interval": self.interval,
            "start_time": self.start_time,
            "db_path": self.db_path,
            "experiment_dir": self.experiment_dir,
            "rehearsal": {"name": self.rehearsal.name, "strategy": self.rehearsal.strategy_name},
            "benchmark": {"name": self.benchmark.name, "strategy": self.benchmark.strategy_name},
        }


def load_plan(
    rehearsal_path: str | Path, benchmark_path: str | Path, *, name: str | None = None
) -> RehearsalPlan:
    """Load a rehearsal from two config files on disk."""
    return build_plan(load_config(rehearsal_path), load_config(benchmark_path), name=name)


def build_plan(
    rehearsal: ExperimentConfig, benchmark: ExperimentConfig, *, name: str | None = None
) -> RehearsalPlan:
    _require_shared_data(rehearsal, benchmark)
    if rehearsal.experiment_dir != benchmark.experiment_dir:
        raise PlanMismatchError(
            "the rehearsal and benchmark configs must record into the same "
            f"experiment_dir so a report can find both: {rehearsal.experiment_dir!r} "
            f"vs {benchmark.experiment_dir!r}"
        )
    return RehearsalPlan(name=name or rehearsal.name, rehearsal=rehearsal, benchmark=benchmark)


def resolved_for_end_time(config: ExperimentConfig, end_time: int) -> ExperimentConfig:
    """The same config with its window closed off at ``end_time`` instead of the file's own."""
    return dataclasses.replace(
        config,
        data=dataclasses.replace(config.data, end_time=end_time),
        backtest=dataclasses.replace(config.backtest, end_time=end_time),
    )


def _require_shared_data(rehearsal: ExperimentConfig, benchmark: ExperimentConfig) -> None:
    a, b = rehearsal.data, benchmark.data
    mismatches = []
    if a.db_path != b.db_path:
        mismatches.append(f"db_path: {a.db_path!r} vs {b.db_path!r}")
    if a.interval != b.interval:
        mismatches.append(f"interval: {a.interval!r} vs {b.interval!r}")
    if tuple(sorted(a.symbols)) != tuple(sorted(b.symbols)):
        mismatches.append(f"symbols: {sorted(a.symbols)} vs {sorted(b.symbols)}")
    if a.start_time != b.start_time:
        mismatches.append(f"start: {a.start_time} vs {b.start_time}")
    for name in ("initial_cash", "starting_holdings", "external_contributions", "valuation_asset", "assumed_rates"):
        if getattr(rehearsal.backtest, name) != getattr(benchmark.backtest, name):
            mismatches.append(f"portfolio.{name} differs")
    for name in ("costs", "execution"):
        if getattr(rehearsal.backtest, name).describe() != getattr(benchmark.backtest, name).describe():
            mismatches.append(f"{name} differs")
    for name in ("include_quotes", "feed_delay_ms", "max_gap_intervals", "require_all_symbols"):
        if getattr(a, name) != getattr(b, name):
            mismatches.append(f"data.{name} differs")
    if mismatches:
        raise PlanMismatchError(
            "the rehearsal and benchmark configs must read identical data so the "
            "comparison is fair, but they disagree on: " + "; ".join(mismatches)
        )
