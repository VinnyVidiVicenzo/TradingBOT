"""What a strategy is allowed to see and do at one decision time."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from ..binanceus.filters import SymbolRuleBook, SymbolRules
from ..decimals import ZERO
from ..models import CashSource
from .market import MarketView
from .portfolio import Portfolio


class PortfolioView:
    """Read-only portfolio access for strategies.

    Deliberately narrow. A strategy can ask what cash is available in a
    bucket and what it holds; it cannot move anything, and there is no
    accessor that presents a holding as spendable.
    """

    __slots__ = ("_portfolio",)

    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio

    @property
    def valuation_asset(self) -> str:
        return self._portfolio.valuation_asset

    def available_cash(self, asset: str, source: CashSource | None = None) -> Decimal:
        """Spendable cash. Holdings are never included in this number."""
        return self._portfolio.cash.available(asset, source)

    def cash_assets(self) -> tuple[str, ...]:
        return self._portfolio.cash.assets()

    def cumulative_deposits(
        self, asset: str, source: CashSource | None = None
    ) -> Decimal:
        """Total ever deposited into a bucket, ignoring what has been spent.

        Lets a strategy detect that new money has arrived without comparing
        balances, which is unreliable once rounding dust is involved.
        """
        return self._portfolio.cash.cumulative_deposits(asset, source)

    def holding(self, asset: str) -> Decimal:
        return self._portfolio.quantity(asset)

    def holdings(self) -> Mapping[str, Decimal]:
        return {
            asset: position.quantity
            for asset, position in sorted(self._portfolio.positions.items())
            if position.quantity > ZERO
        }

    def average_entry_price(self, asset: str, quote_asset: str) -> Decimal | None:
        return self._portfolio.average_entry_price(asset, quote_asset)


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Everything passed to a strategy on one decision tick."""

    now: int
    market: MarketView
    portfolio: PortfolioView
    rules: SymbolRuleBook
    eligible_symbols: tuple[str, ...]
    decision_index: int
    run_id: str

    def rules_for(self, symbol: str) -> SymbolRules:
        return self.rules.get(symbol)

    def client_order_id(self, strategy_name: str, suffix: str) -> str:
        """Deterministic client order ID.

        Derived only from values that are identical across two runs of the
        same configuration, so repeated runs produce identical IDs -- and so
        an ID collision reflects a real flaw in the strategy schedule rather
        than run-to-run noise. The run ID is deliberately excluded.
        """
        return f"{strategy_name}-{self.decision_index:06d}-{suffix}"
