"""Deterministic, chronologically stepped backtesting.

There is no live-execution counterpart to this package in V1. The simulator
produces :class:`~crypto_research.backtest.orders.SimulatedOrder` records and
nothing else; no object here has a method that talks to an exchange.
"""

from .context import PortfolioView, StrategyContext
from .costs import (
    CommissionAssetPolicy,
    CostModels,
    FeeSchedule,
    FixedSlippageModel,
    FixedSpreadModel,
    FlatFeeModel,
    LatencyModel,
    ParticipationSlippageModel,
)
from .engine import BacktestConfig, Backtester, EquityPoint, RunResult
from .execution import (
    ExecutionConfig,
    ExecutionSimulator,
    FixedFractionFillModel,
    FullFillModel,
    VolumeCapFillModel,
)
from .market import MarketState, MarketView
from .metrics import RunMetrics, compare, compute_metrics
from .orders import (
    ClientOrderIdRegistry,
    OrderIntent,
    OrderSide,
    OrderStatus,
    RejectionReason,
    SimulatedOrder,
)
from .portfolio import CashLedger, Portfolio, Position, RateView

__all__ = [
    "BacktestConfig",
    "Backtester",
    "CashLedger",
    "ClientOrderIdRegistry",
    "CommissionAssetPolicy",
    "CostModels",
    "EquityPoint",
    "ExecutionConfig",
    "ExecutionSimulator",
    "FeeSchedule",
    "FixedFractionFillModel",
    "FixedSlippageModel",
    "FixedSpreadModel",
    "FlatFeeModel",
    "FullFillModel",
    "LatencyModel",
    "MarketState",
    "MarketView",
    "OrderIntent",
    "OrderSide",
    "OrderStatus",
    "ParticipationSlippageModel",
    "Portfolio",
    "PortfolioView",
    "Position",
    "RateView",
    "RejectionReason",
    "RunMetrics",
    "RunResult",
    "SimulatedOrder",
    "StrategyContext",
    "VolumeCapFillModel",
    "compare",
    "compute_metrics",
]
