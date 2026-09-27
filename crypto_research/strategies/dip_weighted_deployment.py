"""Buy more when the price has fallen.

Same finite pool and same buy dates as Benchmark 1, but the amount spent on
each date is scaled by how far each asset sits below its own recent high. Fall
further, buy more.

The design deliberately keeps one thing fixed: the pool is fully deployed by
the final scheduled date either way. Without that, a dip-buying rule that
simply ends up holding more cash would look different from steady buying for a
reason that has nothing to do with dip buying, and the comparison would be
worthless.

``tilt_strength = 0`` reduces this exactly to Benchmark 1, which is asserted by
a test. That is the safety net: any difference in results has to come from the
tilt and nothing else.

How far below the high is measured only from bars the strategy can already see.
The engine's MarketView refuses to hand over anything later, so a fall cannot be
measured against a peak that has not happened yet.
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
class DipWeightedDeployment(BaseStrategy):
    """Deploy a fixed pool on a fixed schedule, spending more after falls."""

    universe: AssetUniverse
    quote_asset: str = "USDT"
    tranche_count: int = 12
    cadence_days: int = 7
    # Days of history the "recent high" is measured over.
    lookback_days: int = 90
    # 0 = spend the same every time (identical to Benchmark 1).
    # 2 = spend triple after a 100% fall, double after 50%, and so on.
    tilt_strength: Decimal = dec("2")
    # Bounds, so one very deep fall cannot consume the entire pool at once and
    # so a rising market still keeps buying something.
    min_tilt: Decimal = dec("0.5")
    max_tilt: Decimal = dec("3")

    name: str = field(default="dip_weighted_deployment", init=False)
    version: str = field(default="1", init=False)

    _pool: Decimal = field(default=ZERO, init=False, repr=False)
    _dates_done: int = field(default=0, init=False, repr=False)
    _next_due: int | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.tranche_count < 1:
            raise ConfigError("tranche_count must be at least 1")
        if self.cadence_days < 0:
            raise ConfigError("cadence_days cannot be negative")
        if self.lookback_days < 2:
            raise ConfigError("lookback_days must be at least 2")
        if self.tilt_strength < 0:
            raise ConfigError("tilt_strength cannot be negative")
        if not (0 < self.min_tilt <= self.max_tilt):
            raise ConfigError("need 0 < min_tilt <= max_tilt")

    # -- the tilt ----------------------------------------------------------

    def fall_from_high(self, ctx: StrategyContext, symbol: str) -> Decimal:
        """How far below its recent high this asset is, as a fraction.

        0 means at or above the recent high; 0.4 means 40% below it. Computed
        from visible bars only.
        """
        bars = ctx.market.history(symbol, self.lookback_days)
        if not bars:
            return ZERO
        peak = max(bar.close for bar in bars)
        current = bars[-1].close
        if peak <= 0 or current >= peak:
            return ZERO
        return div(peak - current, peak)

    def tilt_for(self, ctx: StrategyContext, symbol: str) -> Decimal:
        raw = Decimal(1) + mul(self.tilt_strength, self.fall_from_high(ctx, symbol))
        return max(self.min_tilt, min(self.max_tilt, raw))

    # -- the schedule ------------------------------------------------------

    def on_decision(self, ctx: StrategyContext) -> Sequence[OrderIntent]:
        if not self._started:
            self._pool = ctx.portfolio.available_cash(self.quote_asset, CashSource.INITIAL)
            self._next_due = ctx.now
            self._started = True

        if self._dates_done >= self.tranche_count:
            return ()
        if self._next_due is None or ctx.now < self._next_due:
            return ()

        available = ctx.portfolio.available_cash(self.quote_asset, CashSource.INITIAL)
        if available <= 0:
            self._dates_done += 1
            self._advance(ctx)
            return ()

        dates_left = self.tranche_count - self._dates_done
        steady = div(available, Decimal(dates_left))

        tilts = {symbol: self.tilt_for(ctx, symbol) for symbol in self.universe.symbols}
        wanted = {
            symbol: mul(mul(steady, self.universe.weight(symbol)), tilts[symbol])
            for symbol in self.universe.symbols
        }
        total = sum(wanted.values(), ZERO)

        # On the final date, spend whatever is left, so both this strategy and
        # the steady one finish fully invested and stay comparable.
        budget = available if dates_left == 1 else min(total, available)
        if total > 0 and budget != total:
            scale = div(budget, total)
            wanted = {symbol: mul(amount, scale) for symbol, amount in wanted.items()}

        intents: list[OrderIntent] = []
        for symbol in self.universe.symbols:
            amount = wanted[symbol]
            if amount <= 0:
                continue
            intents.append(
                OrderIntent(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    client_order_id=ctx.client_order_id(
                        self.name, f"d{self._dates_done:03d}-{symbol}"
                    ),
                    quote_quantity=amount,
                    funding_source=CashSource.INITIAL,
                    tag=f"date={self._dates_done};tilt={format_decimal(tilts[symbol])}",
                )
            )

        self._dates_done += 1
        self._advance(ctx)
        return intents

    def _advance(self, ctx: StrategyContext) -> None:
        self._next_due = (
            add_days(ctx.now, self.cadence_days) if self.cadence_days else ctx.now
        )

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "universe": self.universe.describe(),
            "quote_asset": self.quote_asset,
            "tranche_count": self.tranche_count,
            "cadence_days": self.cadence_days,
            "lookback_days": self.lookback_days,
            "tilt_strength": format_decimal(self.tilt_strength),
            "min_tilt": format_decimal(self.min_tilt),
            "max_tilt": format_decimal(self.max_tilt),
            "funding_source": CashSource.INITIAL.value,
            "behaviour": (
                "Deploys a fixed pre-existing pool on a fixed schedule, spending "
                "more on dates when an asset sits further below its recent high. "
                "Fully deployed by the final date. tilt_strength=0 is identical "
                "to benchmark1_staged_deployment."
            ),
        }


def build(
    symbols: Sequence[str],
    *,
    weights: dict[str, object] | None = None,
    quote_asset: str = "USDT",
    tranche_count: int = 12,
    cadence_days: int = 7,
    lookback_days: int = 90,
    tilt_strength: object = "2",
    min_tilt: object = "0.5",
    max_tilt: object = "3",
) -> DipWeightedDeployment:
    return DipWeightedDeployment(
        universe=AssetUniverse.from_config(symbols, weights),
        quote_asset=quote_asset,
        tranche_count=int(tranche_count),
        cadence_days=int(cadence_days),
        lookback_days=int(lookback_days),
        tilt_strength=dec(tilt_strength),
        min_tilt=dec(min_tilt),
        max_tilt=dec(max_tilt),
    )
