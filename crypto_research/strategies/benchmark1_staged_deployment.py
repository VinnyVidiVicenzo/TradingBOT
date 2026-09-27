"""Benchmark 1 -- staged deployment of a finite initial cash pool.

This is the "I already have some cash and I do not want to put it all in on one
day" benchmark. The pool is fixed and known at the start; the strategy spends
it in equal tranches on a fixed cadence and then stops, because there is
nothing left.

It draws exclusively on the INITIAL cash bucket. If an external contribution
lands during the window, this strategy will not touch it -- that is Benchmark
2's job, and merging the two would make it impossible to tell whether a result
came from deployment timing or from new money arriving.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from ..backtest.context import StrategyContext
from ..backtest.orders import OrderIntent, OrderSide
from ..binanceus.filters import OrderType
from ..decimals import ZERO, dec, div, format_decimal, mul
from ..errors import ConfigError
from ..models import CashSource
from ..timeutil import add_days
from .base import AssetUniverse, BaseStrategy


@dataclass(slots=True)
class StagedDeployment(BaseStrategy):
    """Spend a fixed initial pool in ``tranche_count`` equal slices."""

    universe: AssetUniverse
    quote_asset: str = "USD"
    tranche_count: int = 12
    cadence_days: int = 7
    # Deploy at most this fraction of the pool per tranche regardless of
    # tranche_count. None means the tranche size is purely pool/count.
    max_tranche_fraction: Decimal | None = None
    name: str = field(default="benchmark1_staged_deployment", init=False)
    version: str = field(default="1", init=False)

    _pool: Decimal = field(default=ZERO, init=False, repr=False)
    _tranches_sent: int = field(default=0, init=False, repr=False)
    _next_due: int | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.tranche_count < 1:
            raise ConfigError("tranche_count must be at least 1")
        if self.cadence_days < 0:
            raise ConfigError("cadence_days cannot be negative")

    def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        if not self._started:
            # The pool is measured once, at the first decision, so the tranche
            # size does not drift as the balance is spent down.
            self._pool = ctx.portfolio.available_cash(self.quote_asset, CashSource.INITIAL)
            self._next_due = ctx.now
            self._started = True

        if self._tranches_sent >= self.tranche_count:
            return ()
        if self._next_due is None or ctx.now < self._next_due:
            return ()
        if self._pool <= 0:
            return ()

        tranche = div(self._pool, Decimal(self.tranche_count))
        if self.max_tranche_fraction is not None:
            tranche = min(tranche, mul(self._pool, self.max_tranche_fraction))

        # Never plan to spend more than the bucket actually holds. Without
        # this the final tranche would be rejected for insufficient balance
        # whenever fees have eaten into the pool.
        available = ctx.portfolio.available_cash(self.quote_asset, CashSource.INITIAL)
        tranche = min(tranche, available)
        if tranche <= 0:
            return ()

        intents: list[OrderIntent] = []
        for symbol in self.universe.symbols:
            allocation = mul(tranche, self.universe.weight(symbol))
            if allocation <= 0:
                continue
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    client_order_id=ctx.client_order_id(
                        self.name, f"t{self._tranches_sent:03d}-{symbol}"
                    ),
                    quote_quantity=allocation,
                    funding_source=CashSource.INITIAL,
                    tag=f"tranche={self._tranches_sent}",
                )
            )

        self._tranches_sent += 1
        self._next_due = add_days(ctx.now, self.cadence_days) if self.cadence_days else ctx.now
        return intents

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "universe": self.universe.describe(),
            "quote_asset": self.quote_asset,
            "tranche_count": self.tranche_count,
            "cadence_days": self.cadence_days,
            "max_tranche_fraction": (
                None
                if self.max_tranche_fraction is None
                else format_decimal(self.max_tranche_fraction)
            ),
            "funding_source": CashSource.INITIAL.value,
            "behaviour": (
                "Spends only pre-existing available cash, in equal tranches on a "
                "fixed cadence. Ignores external contributions entirely."
            ),
        }


def build(
    symbols: Sequence[str],
    *,
    weights: dict[str, object] | None = None,
    quote_asset: str = "USD",
    tranche_count: int = 12,
    cadence_days: int = 7,
    max_tranche_fraction: object | None = None,
) -> StagedDeployment:
    return StagedDeployment(
        universe=AssetUniverse.from_config(symbols, weights),
        quote_asset=quote_asset,
        tranche_count=int(tranche_count),
        cadence_days=int(cadence_days),
        max_tranche_fraction=(
            None if max_tranche_fraction is None else dec(max_tranche_fraction)
        ),
    )
