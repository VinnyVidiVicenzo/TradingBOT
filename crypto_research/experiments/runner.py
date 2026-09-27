"""Turn a config file into a recorded experiment run."""

from __future__ import annotations

import platform
import traceback
from pathlib import Path
from typing import Any

from ..backtest.engine import Backtester, RunResult
from ..backtest.metrics import RunMetrics, compute_metrics, equity_curve_rows
from ..binanceus.filters import SymbolRuleBook
from ..config import ExperimentConfig, load_config
from ..datafeed.loader import load_dataset
from ..decimals import format_decimal
from ..errors import ConfigError, CryptoResearchError
from ..storage.database import open_database
from ..storage.repositories import RuleSnapshotRepository
from ..strategies import build_strategy
from ..timeutil import now_ms, to_iso
from .registry import ExperimentRegistry, RunRecord, RunStatus, config_hash, git_state


def run_experiment_file(
    path: str | Path, *, registry: ExperimentRegistry | None = None
) -> tuple[RunRecord, RunResult | None, RunMetrics | None]:
    return run_experiment(load_config(path), registry=registry)


def run_experiment(
    config: ExperimentConfig, *, registry: ExperimentRegistry | None = None
) -> tuple[RunRecord, RunResult | None, RunMetrics | None]:
    """Run one experiment and record it, whether or not it succeeds."""
    registry = registry or ExperimentRegistry(config.experiment_dir)
    resolved = config.describe()
    digest = config_hash(resolved)
    run_id = registry.allocate_run_id(digest)
    commit, dirty = git_state()

    skeleton: dict[str, Any] = {
        "run_id": run_id,
        "created_at": now_ms(),
        "experiment_name": config.name,
        "description": config.description,
        "strategy_name": config.strategy_name,
        "strategy_params": dict(config.strategy_params),
        "config": resolved,
        "config_sha256": digest,
        "eligible_symbols": list(config.data.symbols),
        "starting_holdings": resolved["backtest"]["starting_holdings"],
        "starting_cash": resolved["backtest"]["initial_cash"],
        "external_contributions": resolved["backtest"]["external_contributions"],
        "fee_assumptions": resolved["backtest"]["costs"]["fees"],
        "spread_assumptions": resolved["backtest"]["costs"]["spread"],
        "slippage_assumptions": resolved["backtest"]["costs"]["slippage"],
        "latency_assumptions": resolved["backtest"]["costs"]["latency"],
        "execution_settings": resolved["backtest"]["execution"],
        "date_range": {
            "start_time": config.data.start_time,
            "end_time": config.data.end_time,
            "start_iso": to_iso(config.data.start_time),
            "end_iso": to_iso(config.data.end_time),
            "interval": config.data.interval,
        },
        "git_commit": commit,
        "git_dirty": dirty,
        "code_version": _code_version(),
        "tool_versions": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
    }

    try:
        result, metrics = _execute(config, run_id)
    except (CryptoResearchError, Exception) as exc:  # noqa: BLE001 - recorded, then re-raised
        record = RunRecord(
            status=RunStatus.FAILED,
            strategy_version="unknown",
            dataset_version=None,
            rate_assumptions_used=(),
            metrics=None,
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
            **skeleton,
        )
        registry.write(record)
        return record, None, None

    record = RunRecord(
        status=RunStatus.COMPLETED,
        strategy_version=result.strategy_version,
        dataset_version=(
            result.dataset_version.to_dict() if result.dataset_version else None
        ),
        rate_assumptions_used=[
            f"{base}->{quote}" for base, quote in result.used_rate_assumptions
        ],
        metrics=metrics.to_dict(),
        warnings=list(result.warnings),
        **skeleton,
    )
    registry.write(
        record,
        equity_curve_rows=equity_curve_rows(result.equity_curve),
        order_rows=[order.to_dict() for order in result.orders],
        cash_ledger_rows=[
            {
                "as_of": entry.as_of,
                "asset": entry.asset,
                "amount": format_decimal(entry.amount),
                "source": entry.source.value,
                "reason": entry.reason,
            }
            for entry in (result.portfolio.cash.entries if result.portfolio else ())
        ],
    )
    return record, result, metrics


def _execute(config: ExperimentConfig, run_id: str) -> tuple[RunResult, RunMetrics]:
    with open_database(config.data.db_path) as db:
        rules = RuleSnapshotRepository(db).latest()
        if not rules:
            raise ConfigError(
                f"no exchangeInfo snapshots in {config.data.db_path}; run "
                "`python -m crypto_research data refresh-rules` first"
            )
        rule_book = SymbolRuleBook(rules)
        missing = [s for s in config.data.symbols if s not in rule_book]
        if missing:
            raise ConfigError(
                f"no Binance.US trading rules stored for {', '.join(missing)}; "
                "download them before running"
            )

        dataset = load_dataset(
            db,
            config.data.symbols,
            config.data.interval,
            start_time=config.data.start_time,
            end_time=config.data.end_time,
            include_quotes=config.data.include_quotes,
            require_all_symbols=config.data.require_all_symbols,
            max_gap_intervals=config.data.max_gap_intervals,
            feed_delay_ms=config.data.feed_delay_ms,
        )

    strategy = build_strategy(config.strategy_name, config.strategy_params)
    backtester = Backtester(
        dataset=dataset,
        rule_book=rule_book,
        config=config.backtest,
        fill_model=config.fill_model,
    )
    result = backtester.run(strategy, run_id=run_id)
    return result, compute_metrics(result)


def _code_version() -> str:
    from .. import __version__

    return __version__
