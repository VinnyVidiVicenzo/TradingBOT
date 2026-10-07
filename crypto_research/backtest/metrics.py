"""Run metrics.

Deliberately absent: win rate, and any per-trade profit statistic built on it.
V1 evaluates *accumulation*, where the position is only ever opened. Every buy
in an uptrend is a "win" and every buy in a downtrend is a "loss", so a win
rate here measures the market, not the strategy. The metrics that do
discriminate between accumulation plans are average entry price, cost drag
(fees plus slippage), uninvested cash, and time-weighted exposure -- so those
are the ones computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Sequence

from ..decimals import ZERO, div, format_decimal, mul
from .engine import EquityPoint, RunResult
from .orders import OrderSide, OrderStatus


@dataclass(slots=True)
class RunMetrics:
    """Everything worth comparing between two runs."""

    valuation_asset: str

    starting_portfolio_value: Decimal = ZERO
    ending_portfolio_value: Decimal = ZERO
    total_external_contributions: Decimal = ZERO
    total_capital_in: Decimal = ZERO
    net_profit: Decimal = ZERO
    net_return: Decimal = ZERO
    time_weighted_return: Decimal = ZERO
    max_drawdown: Decimal = ZERO
    max_drawdown_start: int = 0
    max_drawdown_end: int = 0

    total_purchased_capital: Decimal = ZERO
    total_sold_capital: Decimal = ZERO
    total_fees_in_quote: Decimal = ZERO
    total_fees_by_asset: dict[str, Decimal] = field(default_factory=dict)
    estimated_slippage_cost: Decimal = ZERO
    estimated_spread_cost: Decimal = ZERO
    total_cost_drag: Decimal = ZERO
    turnover: Decimal = ZERO

    average_entry_price_by_asset: dict[str, Decimal] = field(default_factory=dict)
    acquired_quantity_by_asset: dict[str, Decimal] = field(default_factory=dict)
    ending_holdings: dict[str, Decimal] = field(default_factory=dict)

    time_weighted_exposure: Decimal = ZERO
    uninvested_cash_by_asset: dict[str, Decimal] = field(default_factory=dict)
    uninvested_cash_value: Decimal = ZERO
    uninvested_cash_fraction: Decimal = ZERO

    attempted_orders: int = 0
    accepted_orders: int = 0
    partially_filled_orders: int = 0
    rejected_orders: int = 0
    rejections_by_reason: dict[str, int] = field(default_factory=dict)
    fill_rate: Decimal = ZERO

    decision_count: int = 0
    first_mark_time: int = 0
    last_mark_time: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        def d(value: Decimal) -> str:
            return format_decimal(value)

        return {
            "valuation_asset": self.valuation_asset,
            "starting_portfolio_value": d(self.starting_portfolio_value),
            "ending_portfolio_value": d(self.ending_portfolio_value),
            "total_external_contributions": d(self.total_external_contributions),
            "total_capital_in": d(self.total_capital_in),
            "net_profit": d(self.net_profit),
            "net_return": d(self.net_return),
            "time_weighted_return": d(self.time_weighted_return),
            "max_drawdown": d(self.max_drawdown),
            "max_drawdown_start": self.max_drawdown_start,
            "max_drawdown_end": self.max_drawdown_end,
            "total_purchased_capital": d(self.total_purchased_capital),
            "total_sold_capital": d(self.total_sold_capital),
            "total_fees_in_quote": d(self.total_fees_in_quote),
            "total_fees_by_asset": {
                asset: d(amount) for asset, amount in sorted(self.total_fees_by_asset.items())
            },
            "estimated_slippage_cost": d(self.estimated_slippage_cost),
            "estimated_spread_cost": d(self.estimated_spread_cost),
            "total_cost_drag": d(self.total_cost_drag),
            "turnover": d(self.turnover),
            "average_entry_price_by_asset": {
                asset: d(price)
                for asset, price in sorted(self.average_entry_price_by_asset.items())
            },
            "acquired_quantity_by_asset": {
                asset: d(qty)
                for asset, qty in sorted(self.acquired_quantity_by_asset.items())
            },
            "ending_holdings": {
                asset: d(qty) for asset, qty in sorted(self.ending_holdings.items())
            },
            "time_weighted_exposure": d(self.time_weighted_exposure),
            "uninvested_cash_by_asset": {
                asset: d(amount)
                for asset, amount in sorted(self.uninvested_cash_by_asset.items())
            },
            "uninvested_cash_value": d(self.uninvested_cash_value),
            "uninvested_cash_fraction": d(self.uninvested_cash_fraction),
            "attempted_orders": self.attempted_orders,
            "accepted_orders": self.accepted_orders,
            "partially_filled_orders": self.partially_filled_orders,
            "rejected_orders": self.rejected_orders,
            "rejections_by_reason": dict(sorted(self.rejections_by_reason.items())),
            "fill_rate": d(self.fill_rate),
            "decision_count": self.decision_count,
            "first_mark_time": self.first_mark_time,
            "last_mark_time": self.last_mark_time,
            "win_rate": None,
            "win_rate_omitted_because": (
                "V1 measures accumulation, not entries and exits; a win rate here "
                "would describe the market direction rather than the strategy."
            ),
            "notes": list(self.notes),
        }


def compute_metrics(result: RunResult) -> RunMetrics:
    """Derive every V1 metric from a completed run."""
    config = result.config
    metrics = RunMetrics(valuation_asset=config.valuation_asset)
    metrics.decision_count = result.decision_count
    metrics.notes.extend(result.warnings)

    curve = result.equity_curve
    if curve:
        # A contribution credited at or before the first mark is already inside
        # that mark's total value *and* carried on it as an inflow. Take it back
        # out here so it is counted once, as a contribution, and not also as
        # starting value.
        metrics.starting_portfolio_value = (
            result.starting_portfolio_value if result.starting_portfolio_value is not None
            else curve[0].total_value - curve[0].contribution_inflow
        )
        metrics.ending_portfolio_value = curve[-1].total_value
        metrics.first_mark_time = curve[0].time
        metrics.last_mark_time = curve[-1].time
        metrics.total_external_contributions = sum(
            (point.contribution_inflow for point in curve), ZERO
        )
        metrics.max_drawdown, metrics.max_drawdown_start, metrics.max_drawdown_end = (
            _max_drawdown(curve)
        )
        metrics.time_weighted_return = _time_weighted_return(curve)
        if result.starting_portfolio_value is not None:
            metrics.time_weighted_return = curve[-1].nav_per_unit - Decimal(1)
            # Include the pre-trade unit price, so immediate costs are a fall.
            baseline = EquityPoint(curve[0].time, ZERO, ZERO, ZERO, ZERO, Decimal(1))
            metrics.max_drawdown, metrics.max_drawdown_start, metrics.max_drawdown_end = (
                _max_drawdown([baseline, *curve])
            )
        metrics.time_weighted_exposure = _time_weighted_exposure(curve)

    metrics.total_capital_in = (
        metrics.starting_portfolio_value + metrics.total_external_contributions
    )
    metrics.net_profit = metrics.ending_portfolio_value - metrics.total_capital_in
    if metrics.total_capital_in > 0:
        metrics.net_return = div(metrics.net_profit, metrics.total_capital_in)
    else:
        metrics.notes.append(
            "net_return is zero because no capital entered the run; a strategy with "
            "no starting value and no contributions has nothing to return on."
        )

    # -- order statistics --------------------------------------------------

    traded_notional = ZERO
    for order in result.orders:
        metrics.attempted_orders += 1
        if order.status is OrderStatus.REJECTED:
            metrics.rejected_orders += 1
            reason = order.rejection_reason.value if order.rejection_reason else "UNKNOWN"
            metrics.rejections_by_reason[reason] = (
                metrics.rejections_by_reason.get(reason, 0) + 1
            )
            continue

        metrics.accepted_orders += 1
        if order.status is OrderStatus.PARTIALLY_FILLED:
            metrics.partially_filled_orders += 1

        traded_notional += order.filled_quote_quantity
        if order.intent.side is OrderSide.BUY:
            metrics.total_purchased_capital += order.filled_quote_quantity
        else:
            metrics.total_sold_capital += order.filled_quote_quantity

        metrics.total_fees_in_quote += order.fee_in_quote
        for asset, amount in order.fees_by_asset.items():
            metrics.total_fees_by_asset[asset] = (
                metrics.total_fees_by_asset.get(asset, ZERO) + amount
            )
        metrics.estimated_slippage_cost += order.slippage_cost_quote
        metrics.estimated_spread_cost += order.spread_cost_quote

    if metrics.attempted_orders:
        metrics.fill_rate = div(
            Decimal(metrics.accepted_orders), Decimal(metrics.attempted_orders)
        )
    metrics.total_cost_drag = (
        metrics.total_fees_in_quote
        + metrics.estimated_slippage_cost
        + metrics.estimated_spread_cost
    )

    average_value = _average_value(curve)
    if average_value > 0:
        metrics.turnover = div(traded_notional, average_value)

    # -- portfolio ---------------------------------------------------------

    portfolio = result.portfolio
    if portfolio is not None:
        quote_asset = config.valuation_asset
        for asset, position in sorted(portfolio.positions.items()):
            if position.quantity != 0:
                metrics.ending_holdings[asset] = position.quantity
            if position.acquired_quantity > 0:
                metrics.acquired_quantity_by_asset[asset] = position.acquired_quantity
                average = position.average_entry_price(quote_asset)
                if average is not None:
                    metrics.average_entry_price_by_asset[asset] = average
                else:
                    metrics.notes.append(
                        f"no {quote_asset}-denominated average entry price for {asset}: "
                        f"it was acquired with "
                        f"{sorted(position.cost_by_quote_asset)} instead"
                    )

        metrics.uninvested_cash_by_asset = dict(portfolio.cash.totals_by_asset())
        if curve:
            metrics.uninvested_cash_value = curve[-1].cash_value
            if curve[-1].total_value > 0:
                metrics.uninvested_cash_fraction = div(
                    curve[-1].cash_value, curve[-1].total_value
                )

    return metrics


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _max_drawdown(curve: Sequence[EquityPoint]) -> tuple[Decimal, int, int]:
    """Largest peak-to-trough fall in NAV per unit.

    Measured on the unit price rather than on total value, so a deposit
    landing mid-drawdown does not read as a recovery.
    """
    peak = ZERO
    peak_time = 0
    worst = ZERO
    worst_start = 0
    worst_end = 0
    for point in curve:
        nav = point.nav_per_unit
        if nav > peak:
            peak = nav
            peak_time = point.time
        if peak > 0:
            drawdown = div(peak - nav, peak)
            if drawdown > worst:
                worst = drawdown
                worst_start = peak_time
                worst_end = point.time
    return worst, worst_start, worst_end


def _time_weighted_return(curve: Sequence[EquityPoint]) -> Decimal:
    if len(curve) < 2:
        return ZERO
    first = curve[0].nav_per_unit
    last = curve[-1].nav_per_unit
    if first <= 0:
        return ZERO
    return div(last, first) - Decimal(1)


def _time_weighted_exposure(curve: Sequence[EquityPoint]) -> Decimal:
    """Share of the run, weighted by elapsed time, spent invested rather than in cash.

    A staged-deployment plan that holds cash for six months and a lump sum
    that deploys on day one can produce similar end values for very different
    reasons; this is the number that separates them.
    """
    if len(curve) < 2:
        if curve and curve[0].total_value > 0:
            return div(curve[0].invested_value, curve[0].total_value)
        return ZERO
    weighted = ZERO
    elapsed = ZERO
    for earlier, later in zip(curve, curve[1:]):
        span = Decimal(later.time - earlier.time)
        if span <= 0:
            continue
        if earlier.total_value > 0:
            weighted += mul(div(earlier.invested_value, earlier.total_value), span)
        elapsed += span
    if elapsed <= 0:
        return ZERO
    return div(weighted, elapsed)


def _average_value(curve: Sequence[EquityPoint]) -> Decimal:
    if not curve:
        return ZERO
    total = sum((point.total_value for point in curve), ZERO)
    return div(total, Decimal(len(curve)))


def equity_curve_rows(curve: Sequence[EquityPoint]) -> list[dict[str, object]]:
    """Flat rows for CSV output."""
    return [
        {
            "time": point.time,
            "total_value": format_decimal(point.total_value),
            "cash_value": format_decimal(point.cash_value),
            "invested_value": format_decimal(point.invested_value),
            "units": format_decimal(point.units),
            "nav_per_unit": format_decimal(point.nav_per_unit),
            "contribution_inflow": format_decimal(point.contribution_inflow),
        }
        for point in curve
    ]


def compare(metrics_by_name: Mapping[str, RunMetrics]) -> str:
    """A short side-by-side table, for eyeballing benchmarks against each other."""
    if not metrics_by_name:
        return "(no runs)"
    rows = [
        ("ending value", lambda m: format_decimal(m.ending_portfolio_value)),
        ("capital in", lambda m: format_decimal(m.total_capital_in)),
        ("net return", lambda m: f"{m.net_return * 100:.2f}%"),
        ("max drawdown", lambda m: f"{m.max_drawdown * 100:.2f}%"),
        ("fees", lambda m: format_decimal(m.total_fees_in_quote)),
        ("slippage", lambda m: format_decimal(m.estimated_slippage_cost)),
        ("spread", lambda m: format_decimal(m.estimated_spread_cost)),
        ("exposure (tw)", lambda m: f"{m.time_weighted_exposure * 100:.1f}%"),
        ("cash left", lambda m: format_decimal(m.uninvested_cash_value)),
        ("orders ok/rej", lambda m: f"{m.accepted_orders}/{m.rejected_orders}"),
    ]
    names = list(metrics_by_name)
    width = max(14, *(len(name) for name in names))
    header = "metric".ljust(16) + "".join(name.rjust(width + 2) for name in names)
    lines = [header, "-" * len(header)]
    for label, getter in rows:
        line = label.ljust(16)
        for name in names:
            line += getter(metrics_by_name[name]).rjust(width + 2)
        lines.append(line)
    return "\n".join(lines)
