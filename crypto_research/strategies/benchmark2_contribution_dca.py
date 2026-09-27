"""Benchmark 2 -- periodic DCA funded only by explicit external contributions.

This is the "I add new money every payday" benchmark. It is a separate
question from Benchmark 1 and is kept separate in code: it draws exclusively
on the EXTERNAL_CONTRIBUTION bucket and cannot see, reserve or spend the
initial pool.

No recurring deposit is assumed. The contributions are whatever the run
configuration explicitly declares, each with its own availability time, and a
contribution cannot be spent before that time because the engine has not
credited it yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from ..backtest.context import StrategyContext
from ..backtest.orders import OrderIntent, OrderSide
from ..binanceus.filters import OrderType
from ..decimals import ZERO, dec, format_decimal, mul
from ..errors import ConfigError
from ..models import CashSource
from ..timeutil import add_days
from .base import AssetUniverse, BaseStrategy


@dataclass(slots=True)
class ContributionDca(BaseStrategy):
    """Deploy contributed cash as it becomes available, or on a fixed cadence.

    ``cadence_days = 0`` deploys at the first decision tick on or after the
    money lands. A positive cadence holds contributed cash until the next
    scheduled buy date, which models "deposits land whenever, I buy on the
    1st" and is a genuinely different plan.
    """

    universe: AssetUniverse
    quote_asset: str = "USD"
    cadence_days: int = 0
    # Skip a buy when the contributed balance is below this, instead of
    # generating an order the exchange would refuse on minimum notional. Set
    # to zero to let those rejections surface in the metrics instead.
    min_deploy_amount: Decimal = ZERO
    name: str = field(default="benchmark2_contribution_dca", init=False)
    version: str = field(default="1", init=False)

    _next_due: int | None = field(default=None, init=False, repr=False)
    _rounds: int = field(default=0, init=False, repr=False)
    _last_seen_contribution_total: Decimal | None = field(
        default=None, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.cadence_days < 0:
            raise ConfigError("cadence_days cannot be negative")
        if self.min_deploy_amount < 0:
            raise ConfigError("min_deploy_amount cannot be negative")

    def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        if self._next_due is None:
            self._next_due = ctx.now
        if ctx.now < self._next_due:
            return ()
        # The buy grid is fixed: it advances on every scheduled date, whether
        # or not there was money to spend. Advancing only on a successful buy
        # would let the schedule drift with deposit timing, which is a
        # different strategy from the one this benchmark is meant to be.
        if self.cadence_days:
            self._next_due = add_days(ctx.now, self.cadence_days)

        # Only contributed cash. Reading the INITIAL bucket here would silently
        # turn this benchmark into Benchmark 1.
        available = ctx.portfolio.available_cash(
            self.quote_asset, CashSource.EXTERNAL_CONTRIBUTION
        )
        if available <= 0 or available < self.min_deploy_amount:
            return ()

        # Rounding down to the lot step leaves a few cents behind after every
        # round. Without this check the strategy would re-offer that same dust
        # on every subsequent tick forever, and the resulting flood of
        # minimum-notional rejections would drown out the real ones.
        #
        # The test is cumulative deposits, not the current balance: a second
        # deposit can leave the balance at a figure it already held, which a
        # balance comparison would mistake for unchanged dust and skip.
        contributed_to_date = ctx.portfolio.cumulative_deposits(
            self.quote_asset, CashSource.EXTERNAL_CONTRIBUTION
        )
        if self._last_seen_contribution_total == contributed_to_date:
            return ()
        self._last_seen_contribution_total = contributed_to_date

        intents: list[OrderIntent] = []
        for symbol in self.universe.symbols:
            allocation = mul(available, self.universe.weight(symbol))
            if allocation <= 0:
                continue
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    client_order_id=ctx.client_order_id(
                        self.name, f"r{self._rounds:03d}-{symbol}"
                    ),
                    quote_quantity=allocation,
                    funding_source=CashSource.EXTERNAL_CONTRIBUTION,
                    tag=f"round={self._rounds}",
                )
            )

        self._rounds += 1
        return intents

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "universe": self.universe.describe(),
            "quote_asset": self.quote_asset,
            "cadence_days": self.cadence_days,
            "min_deploy_amount": format_decimal(self.min_deploy_amount),
            "funding_source": CashSource.EXTERNAL_CONTRIBUTION.value,
            "behaviour": (
                "Spends only cash that arrived as a declared external contribution, "
                "and only after its availability time. Never touches the initial pool."
            ),
        }


def build(
    symbols: Sequence[str],
    *,
    weights: dict[str, object] | None = None,
    quote_asset: str = "USD",
    cadence_days: int = 0,
    min_deploy_amount: object = 0,
) -> ContributionDca:
    return ContributionDca(
        universe=AssetUniverse.from_config(symbols, weights),
        quote_asset=quote_asset,
        cadence_days=int(cadence_days),
        min_deploy_amount=dec(min_deploy_amount),
    )
