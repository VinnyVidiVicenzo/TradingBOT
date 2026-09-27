"""Benchmark 0 -- hold the starting portfolio and do nothing.

The floor every other strategy has to clear. It places no orders, pays no
fees and takes no spread, so any strategy that fails to beat it is paying for
activity that did not help.

It deliberately does *not* deploy the initial cash pool: doing nothing means
cash stays cash. That is what makes it the right comparison for Benchmark 1,
which deploys the same pool on a schedule.
"""

from __future__ import annotations

from typing import Sequence

from ..backtest.context import StrategyContext
from ..backtest.orders import OrderIntent
from .base import BaseStrategy


class HoldStartingPortfolio(BaseStrategy):
    name = "benchmark0_hold"
    version = "1"

    def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        return ()

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "behaviour": (
                "No orders. Starting holdings are marked to market; starting cash "
                "and any external contributions remain uninvested."
            ),
        }
