"""Measuring the real cost of crossing the order book, next to what a backtest assumes.

A backtest can only ever assume a spread and a slippage figure (see
``crypto_research.backtest.costs``); it has no order-book history to measure
them from. This module reads the live book -- the same public endpoint
``scripts/trading_cost.py`` already uses for a one-off snapshot -- and starts
building an ongoing record of what a purchase would really have cost, so that
record can eventually be compared against the assumptions.

It only ever reads. Nothing here can place an order, and a failure here never
affects a rehearsal tick: it is invoked separately (see ``cli.py``), not from
inside ``runner.tick()``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.decimals import ZERO, dec, div

DEPTH_LIMIT = 100


@dataclass(frozen=True, slots=True)
class DepthCostMeasurement:
    symbol: str
    at: int
    mid_price: Decimal
    notional_requested: Decimal
    average_price: Decimal | None
    # (average_price / mid_price) - 1. None when the book could not be read or
    # could not fill the requested notional at all -- never guessed at.
    cost_fraction: Decimal | None
    depth_available: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "at": self.at,
            "mid_price": str(self.mid_price),
            "notional_requested": str(self.notional_requested),
            "average_price": None if self.average_price is None else str(self.average_price),
            "cost_fraction": None if self.cost_fraction is None else str(self.cost_fraction),
            "depth_available": self.depth_available,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DepthCostMeasurement":
        return cls(
            symbol=str(payload["symbol"]),
            at=int(payload["at"]),
            mid_price=dec(payload["mid_price"]),
            notional_requested=dec(payload["notional_requested"]),
            average_price=None if payload["average_price"] is None else dec(payload["average_price"]),
            cost_fraction=None if payload["cost_fraction"] is None else dec(payload["cost_fraction"]),
            depth_available=bool(payload["depth_available"]),
        )


def measure_buy_cost(
    client: BinanceUSPublicClient, symbol: str, notional: Decimal, *, at: int
) -> DepthCostMeasurement:
    """How far above the mid price a BUY of ``notional`` would walk the book, right now."""
    if notional <= 0:
        raise ValueError("notional must be positive")
    book = client.depth(symbol, limit=DEPTH_LIMIT)
    asks = [(dec(price), dec(qty)) for price, qty in book.get("asks", ())]
    bids = [(dec(price), dec(qty)) for price, qty in book.get("bids", ())]
    if not asks or not bids:
        return DepthCostMeasurement(symbol, at, ZERO, notional, None, None, depth_available=False)
    mid = (asks[0][0] + bids[0][0]) / 2
    average = _walk(asks, notional)
    if average is None:
        return DepthCostMeasurement(symbol, at, mid, notional, None, None, depth_available=True)
    return DepthCostMeasurement(
        symbol, at, mid, notional, average, div(average, mid) - Decimal(1), depth_available=True,
    )


def _walk(levels: Sequence[tuple[Decimal, Decimal]], notional: Decimal) -> Decimal | None:
    """Volume-weighted average price to fill ``notional`` walking up ``levels``, or None."""
    remaining, cost, quantity = notional, ZERO, ZERO
    for price, size in levels:
        if price <= 0:
            continue
        take = min(size, div(remaining, price))
        if take <= 0:
            continue
        cost += take * price
        quantity += take
        remaining -= take * price
        if remaining <= 0:
            return div(cost, quantity)
    return None


def append(path: str | Path, measurement: DepthCostMeasurement) -> None:
    from crypto_research.jsonl import append_record
    append_record(path, measurement.to_dict())


def read_all(path: str | Path) -> list[DepthCostMeasurement]:
    from crypto_research.jsonl import read_records
    return read_records(path, DepthCostMeasurement.from_dict)
