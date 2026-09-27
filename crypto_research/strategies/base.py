"""The strategy interface.

A strategy is a pure function of the context it is handed. It receives no
database handle, no client, no dataset and no clock; it returns intents and
nothing else. That is what makes a run reproducible, and it is also the
boundary that a future model-driven strategy will sit behind -- see
docs/trading-bot/SAFETY.md on why a model must never be able to reach past it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Protocol, Sequence, runtime_checkable

from ..backtest.context import StrategyContext
from ..backtest.orders import OrderIntent
from ..decimals import ZERO, dec, div, format_decimal
from ..errors import ConfigError


@runtime_checkable
class Strategy(Protocol):
    """What the backtester requires of a strategy."""

    name: str
    version: str

    def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]: ...

    def describe(self) -> dict[str, object]: ...


@dataclass(slots=True)
class AssetUniverse:
    """The candidate assets for a run, with their target weights.

    Neither the asset list nor the weighting is decided by this codebase. Both
    come from configuration, both are recorded in the experiment record, and
    equal weighting is only the fallback when no weights are given -- it is
    not a conclusion.
    """

    symbols: tuple[str, ...]
    weights: dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.symbols:
            raise ConfigError("a strategy universe needs at least one symbol")
        if len(set(self.symbols)) != len(self.symbols):
            raise ConfigError(f"duplicate symbols in universe: {self.symbols}")

        if not self.weights:
            equal = div(Decimal(1), Decimal(len(self.symbols)))
            self.weights = {symbol: equal for symbol in self.symbols}
            return

        unknown = set(self.weights) - set(self.symbols)
        if unknown:
            raise ConfigError(f"weights given for symbols not in the universe: {sorted(unknown)}")
        missing = set(self.symbols) - set(self.weights)
        if missing:
            raise ConfigError(f"no weight given for {sorted(missing)}")
        total = sum(self.weights.values(), ZERO)
        if total <= 0:
            raise ConfigError("weights must sum to a positive number")
        # Normalized rather than rejected, so a config can be written as
        # 1/1/1 or 40/30/30 without ceremony. The normalization is recorded.
        self.weights = {symbol: div(weight, total) for symbol, weight in self.weights.items()}

    def weight(self, symbol: str) -> Decimal:
        return self.weights[symbol]

    def describe(self) -> dict[str, object]:
        return {
            "symbols": list(self.symbols),
            "weights": {
                symbol: format_decimal(weight)
                for symbol, weight in sorted(self.weights.items())
            },
        }

    @classmethod
    def from_config(
        cls, symbols: Sequence[str], weights: Mapping[str, object] | None = None
    ) -> "AssetUniverse":
        return cls(
            symbols=tuple(symbols),
            weights={str(k): dec(v) for k, v in (weights or {}).items()},
        )


class BaseStrategy:
    """Shared plumbing. Subclasses implement ``on_decision``."""

    name = "base"
    version = "0"

    def on_start(self, ctx: StrategyContext) -> None:  # pragma: no cover - hook
        return None

    def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        raise NotImplementedError

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "version": self.version}
