"""The backtester.

Chronologically stepped, event driven, and deterministic: identical data plus
identical configuration produces byte-identical orders and metrics. Nothing in
the loop reads the wall clock, iterates an unordered collection, or consumes
randomness.

What the engine guarantees, in order of how badly each one would distort a
result if it were missing:

* A strategy sees only observations whose observable time is at or before the
  decision time.
* Cash and holdings are separate. Starting coins cannot fund a purchase.
* Initial cash and external contributions are separate buckets.
* A contribution does not exist before its availability time.
* Every order passes the venue filters from real exchangeInfo, or is recorded
  as rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

from ..binanceus.filters import SymbolRuleBook
from ..datafeed.dataset import DatasetVersion, MarketDataSet
from ..decimals import ZERO, div, format_decimal
from ..errors import ConfigError
from ..provenance import rule_identity
from ..models import (
    CashSource,
    ExternalContribution,
    PortfolioHolding,
)
from .context import PortfolioView, StrategyContext
from .costs import CostModels
from .events import EventKind, EventQueue, PendingExecution
from .execution import ExecutionConfig, ExecutionSimulator, FillModel, FullFillModel
from .market import MarketState, MarketView, bar_close_times
from .orders import OrderIntent, SimulatedOrder, OrderStatus, RejectionReason
from .portfolio import Portfolio, RateView


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Everything that defines one run apart from the data and the strategy."""

    start_time: int
    end_time: int
    valuation_asset: str = "USD"
    # Cash you already have and are choosing to deploy over time.
    initial_cash: Mapping[str, Decimal] = field(default_factory=dict)
    # Positions you already hold. Never spendable.
    starting_holdings: Sequence[PortfolioHolding] = ()
    # New money from outside, each with the time it becomes available.
    external_contributions: Sequence[ExternalContribution] = ()
    costs: CostModels = field(default_factory=CostModels)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    # Rates used only when a pair is genuinely absent from the dataset. Each
    # one used is recorded in the run record as an assumption.
    assumed_rates: Mapping[tuple[str, str], Decimal] = field(default_factory=dict)
    # Override the decision grid. Default is every bar-close time in range.
    decision_times: Sequence[int] | None = None
    history_limit: int | None = None
    seed: int = 0

    def __post_init__(self) -> None:
        if self.end_time < self.start_time:
            raise ConfigError("end_time precedes start_time")
        for asset, amount in self.initial_cash.items():
            if asset != asset.upper():
                raise ConfigError(f"cash asset {asset!r} must be upper case")
            if amount < 0:
                raise ConfigError(f"initial cash for {asset} is negative")

    def describe(self) -> dict[str, object]:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "valuation_asset": self.valuation_asset,
            "initial_cash": {
                asset: format_decimal(amount)
                for asset, amount in sorted(self.initial_cash.items())
            },
            "starting_holdings": [
                {
                    "asset": holding.asset,
                    "quantity": format_decimal(holding.quantity),
                    "cost_basis_per_unit": (
                        None
                        if holding.cost_basis_per_unit is None
                        else format_decimal(holding.cost_basis_per_unit)
                    ),
                    "cost_basis_asset": holding.cost_basis_asset,
                    "source": holding.source,
                    "as_of": holding.as_of,
                }
                for holding in self.starting_holdings
            ],
            "external_contributions": [
                {
                    "amount": format_decimal(c.amount),
                    "asset": c.asset,
                    "availability_time": c.availability_time,
                    "contribution_type": c.contribution_type.value,
                    "label": c.label,
                }
                for c in self.external_contributions
            ],
            "costs": self.costs.describe(),
            "execution": self.execution.describe(),
            "assumed_rates": {
                f"{base}->{quote}": format_decimal(rate)
                for (base, quote), rate in sorted(self.assumed_rates.items())
            },
            "history_limit": self.history_limit,
            "seed": self.seed,
        }


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """One mark-to-market observation.

    ``nav_per_unit`` is a fund-style unit price. Contributions issue new units
    at the prevailing NAV rather than inflating the series, so a drawdown
    measured on this column is a drawdown in performance and not an artifact
    of money arriving.
    """

    time: int
    total_value: Decimal
    cash_value: Decimal
    invested_value: Decimal
    units: Decimal
    nav_per_unit: Decimal
    contribution_inflow: Decimal = ZERO


@dataclass(slots=True)
class RunResult:
    """The complete, replayable output of one backtest."""

    strategy_name: str
    strategy_version: str
    config: BacktestConfig
    dataset_version: DatasetVersion | None
    orders: list[SimulatedOrder] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    portfolio: Portfolio | None = None
    eligible_symbols: tuple[str, ...] = ()
    decision_count: int = 0
    used_rate_assumptions: tuple[tuple[str, str], ...] = ()
    warnings: list[str] = field(default_factory=list)
    final_prices: Mapping[str, Decimal] = field(default_factory=dict)
    starting_portfolio_value: Decimal | None = None
    rule_identity: Mapping[str, object] = field(default_factory=dict)

    @property
    def accepted_orders(self) -> list[SimulatedOrder]:
        return [order for order in self.orders if order.accepted]

    @property
    def rejected_orders(self) -> list[SimulatedOrder]:
        return [order for order in self.orders if order.rejected]


class Backtester:
    """Runs one strategy over one dataset under one configuration."""

    def __init__(
        self,
        *,
        dataset: MarketDataSet,
        rule_book: SymbolRuleBook,
        config: BacktestConfig,
        fill_model: FillModel | None = None,
    ) -> None:
        self.dataset = dataset
        self.rule_book = rule_book
        self.config = config
        self.fill_model = fill_model or FullFillModel()

    def run(self, strategy, *, run_id: str = "unregistered") -> RunResult:
        config = self.config
        portfolio = Portfolio(
            starting_holdings=config.starting_holdings,
            valuation_asset=config.valuation_asset,
        )
        for asset, amount in sorted(config.initial_cash.items()):
            if amount > 0:
                portfolio.cash.deposit(
                    asset,
                    amount,
                    CashSource.INITIAL,
                    at=config.start_time,
                    reason="initial available cash",
                )

        market = MarketState(history_limit=config.history_limit)
        simulator = ExecutionSimulator(
            rule_book=self.rule_book,
            costs=config.costs,
            portfolio=portfolio,
            fill_model=self.fill_model,
            config=config.execution,
        )

        result = RunResult(
            strategy_name=getattr(strategy, "name", type(strategy).__name__),
            strategy_version=getattr(strategy, "version", "0"),
            config=config,
            dataset_version=self.dataset.version,
            eligible_symbols=self.dataset.symbols,
            portfolio=portfolio,
            rule_identity=rule_identity(self.rule_book),
        )

        queue = self._build_queue(result)
        nav = _NavTracker()
        pending: dict[int, list[PendingExecution]] = {}
        decision_index = 0
        assumptions: set[tuple[str, str]] = set()

        on_start = getattr(strategy, "on_start", None)
        if callable(on_start):
            on_start(
                StrategyContext(
                    now=config.start_time,
                    market=MarketView(market, config.start_time),
                    portfolio=PortfolioView(portfolio),
                    rules=self.rule_book,
                    eligible_symbols=self.dataset.symbols,
                    decision_index=-1,
                    run_id=run_id,
                )
            )

        for event in queue.drain():
            market.advance_clock(event.time)

            if event.kind is EventKind.BAR_CLOSE:
                market.ingest_bar(event.payload)

            elif event.kind is EventKind.QUOTE:
                market.ingest_quote(event.payload)

            elif event.kind is EventKind.CONTRIBUTION:
                contribution: ExternalContribution = event.payload
                value_before = self._safe_value(portfolio, market, assumptions, result)
                if result.starting_portfolio_value is None:
                    result.starting_portfolio_value = value_before
                nav.seed_if_needed(value_before)
                portfolio.cash.deposit(
                    contribution.asset,
                    contribution.amount,
                    CashSource.EXTERNAL_CONTRIBUTION,
                    at=event.time,
                    reason=contribution.label or contribution.contribution_type.value,
                )
                inflow = self._contribution_value(
                    contribution, market, portfolio, assumptions, result
                )
                nav.on_contribution(value_before, inflow)
                nav.pending_inflow += inflow

            elif event.kind is EventKind.DECISION:
                if result.starting_portfolio_value is None:
                    result.starting_portfolio_value = self._safe_value(
                        portfolio, market, assumptions, result
                    )
                    nav.seed_if_needed(result.starting_portfolio_value)
                context = StrategyContext(
                    now=event.time,
                    market=MarketView(market, event.time),
                    portfolio=PortfolioView(portfolio),
                    rules=self.rule_book,
                    eligible_symbols=self.dataset.symbols,
                    decision_index=decision_index,
                    run_id=run_id,
                )
                intents = strategy.on_decision(context) or ()
                for intent in intents:
                    if not isinstance(intent, OrderIntent):
                        raise ConfigError(
                            f"{result.strategy_name} returned {type(intent).__name__}, "
                            "expected OrderIntent"
                        )
                    submit_time = config.costs.latency.submit_time(event.time)
                    if submit_time > config.end_time:
                        result.orders.append(SimulatedOrder(
                            intent=intent, decision_time=event.time,
                            submit_time=submit_time, status=OrderStatus.REJECTED,
                            rejection_reason=RejectionReason.OUTSIDE_RUN_WINDOW,
                            note="arrival falls after the run end; no simulated fill",
                        ))
                        continue
                    pending.setdefault(submit_time, []).append(
                        PendingExecution(
                            intent=intent,
                            decision_time=event.time,
                            decision_index=decision_index,
                        )
                    )
                    queue.push(submit_time, EventKind.EXECUTION, submit_time)
                    queue.push(submit_time, EventKind.MARK, None)
                decision_index += 1
                result.decision_count = decision_index

            elif event.kind is EventKind.EXECUTION:
                batch = pending.pop(event.payload, [])
                for item in batch:
                    order = simulator.execute(
                        item.intent,
                        decision_time=item.decision_time,
                        submit_time=event.time,
                        market=market,
                    )
                    result.orders.append(order)

            elif event.kind is EventKind.MARK:
                point = self._mark(portfolio, market, nav, event.time, assumptions, result)
                if point is not None:
                    if result.equity_curve and result.equity_curve[-1].time == point.time:
                        previous = result.equity_curve.pop()
                        point = EquityPoint(
                            point.time, point.total_value, point.cash_value,
                            point.invested_value, point.units, point.nav_per_unit,
                            previous.contribution_inflow + point.contribution_inflow,
                        )
                    result.equity_curve.append(point)

        result.used_rate_assumptions = tuple(sorted(assumptions))
        result.final_prices = dict(market.visible_prices())
        if not result.equity_curve or result.equity_curve[-1].time != config.end_time:
            raise ConfigError("the final portfolio could not be valued; run results are unavailable")
        return result

    # -- construction ------------------------------------------------------

    def _build_queue(self, result: RunResult) -> EventQueue:
        config = self.config
        queue = EventQueue()

        # Gaps wide enough to matter are refused by the loader. Any that remain
        # are tolerated, but they still belong in the run record.
        for gap in self.dataset.gaps:
            result.warnings.append(f"data gap: {gap.describe()}")

        for symbol in sorted(self.dataset.bars_by_symbol):
            for bar in self.dataset.bars_by_symbol[symbol]:
                moment = bar.observable_at
                if config.start_time <= moment <= config.end_time:
                    queue.push(moment, EventKind.BAR_CLOSE, bar)

        for symbol in sorted(self.dataset.quotes_by_symbol):
            for quote in self.dataset.quotes_by_symbol[symbol]:
                moment = quote.observable_at
                if config.start_time <= moment <= config.end_time:
                    queue.push(moment, EventKind.QUOTE, quote)

        for contribution in sorted(
            config.external_contributions,
            key=lambda c: (c.availability_time, c.asset, format_decimal(c.amount)),
        ):
            moment = contribution.availability_time
            if moment < config.start_time:
                result.warnings.append(
                    f"contribution of {format_decimal(contribution.amount)} "
                    f"{contribution.asset} is dated before the run start and was ignored"
                )
                continue
            if moment > config.end_time:
                result.warnings.append(
                    f"contribution of {format_decimal(contribution.amount)} "
                    f"{contribution.asset} falls after the run end and was ignored"
                )
                continue
            queue.push(moment, EventKind.CONTRIBUTION, contribution)

        grid = (
            tuple(sorted(set(config.decision_times)))
            if config.decision_times is not None
            else bar_close_times(
                self.dataset.bars_by_symbol,
                start_time=config.start_time,
                end_time=config.end_time,
            )
        )
        if not grid:
            raise ConfigError(
                "no decision times fall inside the run window; check start_time, "
                "end_time and the downloaded bar range"
            )
        for moment in grid:
            if config.start_time <= moment <= config.end_time:
                queue.push(moment, EventKind.DECISION, None)
                queue.push(moment, EventKind.MARK, None)

        queue.push(config.end_time, EventKind.MARK, None)

        return queue

    # -- valuation ---------------------------------------------------------

    def _rates(
        self, market: MarketState, assumptions: set[tuple[str, str]]
    ) -> RateView:
        view = RateView(
            market.visible_prices(),
            valuation_asset=self.config.valuation_asset,
            assumed_rates=self.config.assumed_rates,
        )
        return view

    def _safe_value(
        self,
        portfolio: Portfolio,
        market: MarketState,
        assumptions: set[tuple[str, str]],
        result: RunResult,
    ) -> Decimal:
        rates = self._rates(market, assumptions)
        try:
            value = portfolio.value(rates)
        except Exception as exc:
            raise ConfigError(f"portfolio cannot be valued before cash flow or trading: {exc}") from exc
        assumptions.update(rates.used_assumptions)
        return value

    def _contribution_value(
        self,
        contribution: ExternalContribution,
        market: MarketState,
        portfolio: Portfolio,
        assumptions: set[tuple[str, str]],
        result: RunResult,
    ) -> Decimal:
        rates = self._rates(market, assumptions)
        try:
            value = contribution.amount * rates.rate(
                contribution.asset, self.config.valuation_asset
            )
        except Exception as exc:
            result.warnings.append(
                f"contribution in {contribution.asset} could not be valued in "
                f"{self.config.valuation_asset}: {exc}"
            )
            return ZERO
        assumptions.update(rates.used_assumptions)
        return value

    def _mark(
        self,
        portfolio: Portfolio,
        market: MarketState,
        nav: "_NavTracker",
        now: int,
        assumptions: set[tuple[str, str]],
        result: RunResult,
    ) -> EquityPoint | None:
        rates = self._rates(market, assumptions)
        try:
            total = portfolio.value(rates)
            cash = portfolio.cash_value(rates)
        except Exception as exc:
            message = f"portfolio could not be valued at {now}: {exc}"
            if message not in result.warnings:
                result.warnings.append(message)
            return None
        assumptions.update(rates.used_assumptions)

        nav.seed_if_needed(total)
        inflow = nav.pending_inflow
        nav.pending_inflow = ZERO
        return EquityPoint(
            time=now,
            total_value=total,
            cash_value=cash,
            invested_value=total - cash,
            units=nav.units,
            nav_per_unit=nav.nav(total),
            contribution_inflow=inflow,
        )


class _NavTracker:
    """Fund-style unit accounting, so contributions do not distort drawdown."""

    def __init__(self) -> None:
        self.units = ZERO
        self.pending_inflow = ZERO
        self._seeded = False

    def seed_if_needed(self, value: Decimal) -> None:
        if not self._seeded and value > 0:
            self.units = value
            self._seeded = True

    def on_contribution(self, value_before: Decimal, inflow: Decimal) -> None:
        if inflow <= 0:
            return
        if self.units > 0 and value_before > 0:
            nav = div(value_before, self.units)
            self.units += div(inflow, nav)
        else:
            self.units += inflow
            self._seeded = True

    def nav(self, value: Decimal) -> Decimal:
        if self.units <= 0:
            return Decimal(1)
        return div(value, self.units)
