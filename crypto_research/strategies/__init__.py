"""Strategies available to the backtester.

V1 contains benchmarks only. No machine learning, no news, no language models.
A benchmark has to be beaten out of sample before anything more complicated
earns the right to exist here -- see docs/trading-bot/EXPERIMENTS.md.
"""

from typing import Any, Callable, Mapping

from ..errors import ConfigError
from .base import AssetUniverse, BaseStrategy, Strategy
from .benchmark0_hold import HoldStartingPortfolio
from .benchmark1_staged_deployment import StagedDeployment
from .benchmark1_staged_deployment import build as build_staged_deployment
from .benchmark2_contribution_dca import ContributionDca
from .benchmark2_contribution_dca import build as build_contribution_dca

# Configuration names a strategy by string; nothing imports a strategy class
# by path, so a config file cannot cause arbitrary code to be loaded.
STRATEGY_BUILDERS: Mapping[str, Callable[..., Any]] = {
    "benchmark0_hold": lambda **kwargs: HoldStartingPortfolio(),
    "benchmark1_staged_deployment": build_staged_deployment,
    "benchmark2_contribution_dca": build_contribution_dca,
}


def build_strategy(name: str, params: Mapping[str, Any] | None = None) -> Any:
    """Instantiate a registered strategy by name."""
    try:
        builder = STRATEGY_BUILDERS[name]
    except KeyError as exc:
        raise ConfigError(
            f"unknown strategy {name!r}; registered strategies are "
            f"{sorted(STRATEGY_BUILDERS)}"
        ) from exc
    return builder(**dict(params or {}))


__all__ = [
    "AssetUniverse",
    "BaseStrategy",
    "ContributionDca",
    "HoldStartingPortfolio",
    "STRATEGY_BUILDERS",
    "StagedDeployment",
    "Strategy",
    "build_strategy",
]
