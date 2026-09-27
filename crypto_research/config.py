"""Experiment configuration.

Configs are TOML (or JSON). They are data, not code: a config names a strategy
by a registered string and supplies parameters, so loading one cannot import
or execute anything of its own choosing.

Everything a config says is copied verbatim into the experiment record, which
is why the cost assumptions live here rather than being buried as defaults in
the simulator.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from .backtest.costs import (
    CommissionAssetPolicy,
    CostModels,
    FeeSchedule,
    FixedSlippageModel,
    FixedSpreadModel,
    FlatFeeModel,
    LatencyModel,
    ParticipationSlippageModel,
)
from .backtest.engine import BacktestConfig
from .backtest.execution import (
    ExecutionConfig,
    FillModel,
    FixedFractionFillModel,
    FullFillModel,
    VolumeCapFillModel,
)
from .decimals import dec
from .errors import ConfigError
from .models import (
    CashSource,
    ExternalContribution,
    PortfolioHolding,
    contribution_from_mapping,
    holding_from_mapping,
)
from .timeutil import SUPPORTED_INTERVALS, parse_time

DEFAULT_DB_PATH = "data/market.sqlite"
DEFAULT_EXPERIMENT_DIR = "experiments"


@dataclass(frozen=True, slots=True)
class DataConfig:
    symbols: tuple[str, ...]
    interval: str
    start_time: int
    end_time: int
    db_path: str = DEFAULT_DB_PATH
    include_quotes: bool = False
    require_all_symbols: bool = True
    # Reject a run whose history has a hole wider than this many bars. See
    # DataGap: Binance.US USD pairs are missing 586 days of 2023-2025.
    max_gap_intervals: int = 3
    # Modelled delay between a bar closing at the venue and a strategy
    # being able to act on it. Zero means act at the close.
    feed_delay_ms: int = 0

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ConfigError("[data] needs at least one symbol")
        if self.interval not in SUPPORTED_INTERVALS:
            raise ConfigError(
                f"[data] interval {self.interval!r} is not offered by Binance.US; "
                f"use one of {SUPPORTED_INTERVALS}"
            )
        if self.end_time <= self.start_time:
            raise ConfigError("[data] end must be after start")

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbols": list(self.symbols),
            "interval": self.interval,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "db_path": self.db_path,
            "include_quotes": self.include_quotes,
            "require_all_symbols": self.require_all_symbols,
            "max_gap_intervals": self.max_gap_intervals,
            "feed_delay_ms": self.feed_delay_ms,
        }


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """One complete, reproducible experiment definition."""

    name: str
    description: str
    data: DataConfig
    strategy_name: str
    strategy_params: Mapping[str, Any]
    backtest: BacktestConfig
    fill_model: FillModel
    fill_model_params: Mapping[str, Any]
    experiment_dir: str = DEFAULT_EXPERIMENT_DIR
    source_path: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "data": self.data.to_dict(),
            "strategy": {"name": self.strategy_name, "params": _jsonable(self.strategy_params)},
            "fill_model": self.fill_model.describe(),
            "backtest": self.backtest.describe(),
            "source_path": self.source_path,
        }


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def load_config(path: str | Path) -> ExperimentConfig:
    """Read a .toml or .json experiment configuration."""
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigError(f"config not found: {file_path}")
    text = file_path.read_bytes()
    if file_path.suffix.lower() == ".json":
        payload = json.loads(text.decode("utf-8"))
    else:
        payload = tomllib.loads(text.decode("utf-8"))
    return parse_config(payload, source_path=str(file_path))


def parse_config(payload: Mapping[str, Any], *, source_path: str = "") -> ExperimentConfig:
    experiment = _section(payload, "experiment")
    data = _parse_data(_section(payload, "data", required=True))
    portfolio = _section(payload, "portfolio")
    costs = _parse_costs(_section(payload, "costs"))
    execution = _parse_execution(_section(payload, "execution"))
    fill_model, fill_params = _parse_fill_model(_section(payload, "fill"))
    strategy = _section(payload, "strategy", required=True)

    strategy_name = strategy.get("name")
    if not strategy_name:
        raise ConfigError("[strategy] needs a name")

    holdings = _parse_holdings(portfolio.get("starting_holdings", ()))
    contributions = _parse_contributions(portfolio.get("external_contributions", ()))
    initial_cash = {
        str(asset).upper(): dec(amount)
        for asset, amount in (portfolio.get("initial_cash", {}) or {}).items()
    }
    assumed_rates = _parse_assumed_rates(portfolio.get("assumed_rates", {}) or {})

    backtest = BacktestConfig(
        start_time=data.start_time,
        end_time=data.end_time,
        valuation_asset=str(portfolio.get("valuation_asset", "USD")).upper(),
        initial_cash=initial_cash,
        starting_holdings=holdings,
        external_contributions=contributions,
        costs=costs,
        execution=execution,
        assumed_rates=assumed_rates,
        history_limit=_optional_int(payload.get("history_limit")),
        seed=int(experiment.get("seed", 0)),
    )

    return ExperimentConfig(
        name=str(experiment.get("name", "unnamed")),
        description=str(experiment.get("description", "")),
        data=data,
        strategy_name=str(strategy_name),
        strategy_params=dict(strategy.get("params", {}) or {}),
        backtest=backtest,
        fill_model=fill_model,
        fill_model_params=fill_params,
        experiment_dir=str(experiment.get("experiment_dir", DEFAULT_EXPERIMENT_DIR)),
        source_path=source_path,
        raw=dict(payload),
    )


# --------------------------------------------------------------------------
# section parsers
# --------------------------------------------------------------------------


def _section(payload: Mapping[str, Any], key: str, *, required: bool = False) -> Mapping[str, Any]:
    value = payload.get(key)
    if value is None:
        if required:
            raise ConfigError(f"config is missing the [{key}] section")
        return {}
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{key}] must be a table")
    return value


def _parse_data(section: Mapping[str, Any]) -> DataConfig:
    try:
        symbols = tuple(str(s).upper() for s in section["symbols"])
        interval = str(section["interval"])
        start = parse_time(section["start"])
        end = parse_time(section["end"])
    except KeyError as exc:
        raise ConfigError(f"[data] is missing {exc.args[0]!r}") from exc
    return DataConfig(
        symbols=symbols,
        interval=interval,
        start_time=start,
        end_time=end,
        db_path=str(section.get("db_path", DEFAULT_DB_PATH)),
        include_quotes=bool(section.get("include_quotes", False)),
        require_all_symbols=bool(section.get("require_all_symbols", True)),
        max_gap_intervals=int(section.get("max_gap_intervals", 3)),
        feed_delay_ms=int(section.get("feed_delay_ms", 0)),
    )


def _parse_costs(section: Mapping[str, Any]) -> CostModels:
    fees_section = section.get("fees", {}) or {}
    schedule = FeeSchedule(
        maker_rate=dec(fees_section.get("maker_rate", "0.0040")),
        taker_rate=dec(fees_section.get("taker_rate", "0.0060")),
        source=str(fees_section.get("source", "ASSUMPTION")),
        note=str(
            fees_section.get(
                "note",
                "Assumed Binance.US spot fee rates. Binance.US fees are tiered and "
                "promotional; confirm the rates that applied over the test window.",
            )
        ),
    )
    per_symbol = {
        str(symbol).upper(): FeeSchedule(
            maker_rate=dec(values.get("maker_rate", schedule.maker_rate)),
            taker_rate=dec(values.get("taker_rate", schedule.taker_rate)),
            source=str(values.get("source", "ASSUMPTION")),
            note=str(values.get("note", "")),
        )
        for symbol, values in (fees_section.get("per_symbol", {}) or {}).items()
    }
    policy = CommissionAssetPolicy(
        str(fees_section.get("commission_asset_policy", "RECEIVED_ASSET")).upper()
    )

    spread_section = section.get("spread", {}) or {}
    spread = FixedSpreadModel(
        default_bps=dec(spread_section.get("default_bps", "5")),
        per_symbol_bps={
            str(symbol).upper(): dec(value)
            for symbol, value in (spread_section.get("per_symbol_bps", {}) or {}).items()
        },
        prefer_observed=bool(spread_section.get("prefer_observed", True)),
        note=str(
            spread_section.get(
                "note", "ASSUMPTION: flat half-spread, not measured from order-book history."
            )
        ),
    )

    slippage_section = section.get("slippage", {}) or {}
    model_name = str(slippage_section.get("model", "fixed")).lower()
    if model_name == "participation":
        slippage: Any = ParticipationSlippageModel(
            base_bps=dec(slippage_section.get("base_bps", "2")),
            coefficient_bps=dec(slippage_section.get("coefficient_bps", "50")),
            note=str(slippage_section.get("note", "ASSUMPTION: square-root impact shape.")),
        )
    elif model_name == "fixed":
        slippage = FixedSlippageModel(
            bps=dec(slippage_section.get("bps", "5")),
            per_symbol_bps={
                str(symbol).upper(): dec(value)
                for symbol, value in (slippage_section.get("per_symbol_bps", {}) or {}).items()
            },
            note=str(slippage_section.get("note", "ASSUMPTION: flat slippage.")),
        )
    else:
        raise ConfigError(
            f"unknown slippage model {model_name!r}; use 'fixed' or 'participation'"
        )

    latency_section = section.get("latency", {}) or {}
    latency = LatencyModel(
        decision_to_submit_ms=int(latency_section.get("decision_to_submit_ms", 500)),
        note=str(latency_section.get("note", "ASSUMPTION: constant decision-to-venue latency.")),
    )

    return CostModels(
        fees=FlatFeeModel(
            schedule=schedule, per_symbol=per_symbol, commission_asset_policy=policy
        ),
        spread=spread,
        slippage=slippage,
        latency=latency,
    )


def _parse_execution(section: Mapping[str, Any]) -> ExecutionConfig:
    default_source = section.get("default_funding_source")
    if section.get("allow_sell"):
        raise ConfigError(
            "allow_sell is not available in V1. V1 does not model selling; "
            "enabling it would change the research question, not just a setting."
        )
    return ExecutionConfig(
        max_reference_age_ms=int(section.get("max_reference_age_ms", 90_000)),
        allow_sell=False,
        default_funding_source=(
            CashSource(str(default_source).upper()) if default_source else None
        ),
    )


def _parse_fill_model(section: Mapping[str, Any]) -> tuple[FillModel, Mapping[str, Any]]:
    model_name = str(section.get("model", "full")).lower()
    params = {k: v for k, v in section.items() if k != "model"}
    if model_name == "full":
        return FullFillModel(is_maker=bool(params.get("is_maker", False))), params
    if model_name == "fixed_fraction":
        fractions = tuple(dec(f) for f in params.get("fractions", ["1"]))
        multipliers = tuple(dec(m) for m in params.get("price_multipliers", []) or [])
        return (
            FixedFractionFillModel(
                fractions=fractions,
                price_multipliers=multipliers,
                is_maker=bool(params.get("is_maker", False)),
            ),
            params,
        )
    if model_name == "volume_cap":
        return (
            VolumeCapFillModel(
                max_participation=dec(params.get("max_participation", "0.01")),
                is_maker=bool(params.get("is_maker", False)),
            ),
            params,
        )
    raise ConfigError(
        f"unknown fill model {model_name!r}; use 'full', 'fixed_fraction' or 'volume_cap'"
    )


def _parse_holdings(entries: Sequence[Mapping[str, Any]]) -> tuple[PortfolioHolding, ...]:
    return tuple(holding_from_mapping(entry) for entry in entries)


def _parse_contributions(
    entries: Sequence[Mapping[str, Any]],
) -> tuple[ExternalContribution, ...]:
    return tuple(contribution_from_mapping(entry) for entry in entries)


def _parse_assumed_rates(section: Mapping[str, Any]) -> dict[tuple[str, str], Decimal]:
    """Parse ``{ "USDT->USD" = "1.0" }`` style entries.

    Spelled out as an explicit arrow pair rather than a bare asset so that
    nobody writes ``USDT = 1`` and quietly asserts that a stablecoin is a
    dollar.
    """
    rates: dict[tuple[str, str], Decimal] = {}
    for key, value in section.items():
        if "->" not in key:
            raise ConfigError(
                f"assumed rate key {key!r} must be written as 'BASE->QUOTE', "
                "for example 'USDT->USD'"
            )
        base, quote = (part.strip().upper() for part in key.split("->", 1))
        rates[(base, quote)] = dec(value)
    return rates


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    return value
