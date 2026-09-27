"""Read-only Binance.US public market-data client.

Every method on this class is a GET against a public endpoint. There is no
method that places, amends, cancels or queries an order, and no method that
requires an API key. Adding one would mean adding request signing to
:mod:`crypto_research.binanceus.transport`, which refuses credential headers
outright -- the absence of live trading in V1 is a structural property, not a
convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator, Mapping, Sequence

from ..decimals import dec
from ..errors import ConfigError
from ..models import EXCHANGE_BINANCE_US, ExchangeRuleSnapshot, MarketBar, MarketQuote, Ticker24h
from ..timeutil import SUPPORTED_INTERVALS, interval_to_ms
from .constants import (
    BASE_URL,
    DEFAULT_KLINES_LIMIT,
    MAX_KLINES_LIMIT,
    VALID_DEPTH_LIMITS,
)
from .filters import SymbolRuleBook
from .parsing import (
    parse_book_ticker,
    parse_exchange_info,
    parse_klines,
    parse_ticker_24h,
)
from .transport import PublicTransport


@dataclass(frozen=True, slots=True)
class ExchangeInfoResult:
    """Parsed rules plus the untouched payload that produced them.

    The raw payload is kept so that a later change to a tick size or minimum
    notional can be diffed against what was actually in force when an
    experiment ran.
    """

    raw_payload: Mapping[str, Any]
    snapshots: tuple[ExchangeRuleSnapshot, ...]
    server_time: int
    received_at: int

    def rule_book(self) -> SymbolRuleBook:
        return SymbolRuleBook(self.snapshots)


class BinanceUSPublicClient:
    """Public market data from api.binance.us."""

    exchange = EXCHANGE_BINANCE_US

    def __init__(
        self,
        transport: PublicTransport | None = None,
        *,
        base_url: str = BASE_URL,
    ) -> None:
        self._transport = transport or PublicTransport(base_url=base_url)

    @property
    def transport(self) -> PublicTransport:
        return self._transport

    # -- general -----------------------------------------------------------

    def ping(self) -> bool:
        """Connectivity check. Returns True when the venue answers."""
        self._transport.get("/api/v3/ping")
        return True

    def server_time(self) -> int:
        """Exchange server time in epoch milliseconds."""
        response = self._transport.get("/api/v3/time")
        return int(response.payload["serverTime"])

    def clock_skew_ms(self) -> int:
        """Local clock minus exchange clock, in milliseconds.

        Not used for signing (nothing here signs), but a large skew means
        locally-stamped ``source_received_at`` values are unreliable, which
        matters for the staleness checks in the execution simulator.
        """
        response = self._transport.get("/api/v3/time")
        return response.received_at - int(response.payload["serverTime"])

    # -- exchange rules ----------------------------------------------------

    def exchange_info(self, symbols: Sequence[str] | None = None) -> ExchangeInfoResult:
        """Fetch trading rules. Omit ``symbols`` for the full venue listing."""
        params: dict[str, Any] = {}
        if symbols:
            if len(symbols) == 1:
                params["symbol"] = symbols[0]
            else:
                # The API expects a JSON-ish array literal with no spaces.
                params["symbols"] = "[" + ",".join(f'"{s}"' for s in symbols) + "]"
        response = self._transport.get("/api/v3/exchangeInfo", params)
        payload = response.payload
        snapshots = parse_exchange_info(payload, received_at=response.received_at)
        return ExchangeInfoResult(
            raw_payload=payload,
            snapshots=tuple(snapshots),
            server_time=int(payload.get("serverTime", response.received_at)),
            received_at=response.received_at,
        )

    def symbol_status(self, symbol: str) -> str:
        """Current status string for one symbol, e.g. ``TRADING``."""
        result = self.exchange_info([symbol])
        for snapshot in result.snapshots:
            if snapshot.symbol == symbol:
                return snapshot.status
        raise ConfigError(f"{symbol!r} is not listed on Binance.US")

    # -- prices ------------------------------------------------------------

    def ticker_price(self, symbol: str) -> Decimal:
        response = self._transport.get("/api/v3/ticker/price", {"symbol": symbol})
        return dec(response.payload["price"])

    def ticker_prices(self, symbols: Sequence[str] | None = None) -> dict[str, Decimal]:
        params = _symbols_param(symbols)
        response = self._transport.get("/api/v3/ticker/price", params)
        payload = response.payload
        rows = payload if isinstance(payload, list) else [payload]
        return {str(row["symbol"]): dec(row["price"]) for row in rows}

    def book_ticker(self, symbol: str) -> MarketQuote:
        """Best bid / best ask for one symbol."""
        response = self._transport.get("/api/v3/ticker/bookTicker", {"symbol": symbol})
        return parse_book_ticker(response.payload, received_at=response.received_at)

    def book_tickers(self, symbols: Sequence[str] | None = None) -> list[MarketQuote]:
        params = _symbols_param(symbols)
        response = self._transport.get("/api/v3/ticker/bookTicker", params)
        payload = response.payload
        rows = payload if isinstance(payload, list) else [payload]
        return [parse_book_ticker(row, received_at=response.received_at) for row in rows]

    def ticker_24h(self, symbol: str) -> Ticker24h:
        response = self._transport.get("/api/v3/ticker/24hr", {"symbol": symbol})
        return parse_ticker_24h(response.payload, received_at=response.received_at)

    def tickers_24h(self, symbols: Sequence[str] | None = None) -> list[Ticker24h]:
        params = _symbols_param(symbols)
        response = self._transport.get("/api/v3/ticker/24hr", params)
        payload = response.payload
        rows = payload if isinstance(payload, list) else [payload]
        return [parse_ticker_24h(row, received_at=response.received_at) for row in rows]

    def depth(self, symbol: str, limit: int = 100) -> Mapping[str, Any]:
        """Raw order-book depth. Returned unparsed; V1 only uses top of book."""
        if limit not in VALID_DEPTH_LIMITS:
            raise ConfigError(
                f"depth limit {limit} is not accepted by Binance.US; "
                f"use one of {VALID_DEPTH_LIMITS}"
            )
        response = self._transport.get("/api/v3/depth", {"symbol": symbol, "limit": limit})
        return response.payload

    # -- candlesticks ------------------------------------------------------

    def klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int = DEFAULT_KLINES_LIMIT,
    ) -> list[MarketBar]:
        """One page of candlesticks (at most 1000 rows)."""
        if interval not in SUPPORTED_INTERVALS:
            raise ConfigError(
                f"{interval!r} is not a Binance.US kline interval; "
                f"use one of {SUPPORTED_INTERVALS}"
            )
        if not 1 <= limit <= MAX_KLINES_LIMIT:
            raise ConfigError(f"kline limit must be 1..{MAX_KLINES_LIMIT}, got {limit}")
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        response = self._transport.get("/api/v3/klines", params)
        return parse_klines(
            response.payload,
            symbol=symbol,
            interval=interval,
            received_at=response.received_at,
        )

    def iter_klines(
        self,
        symbol: str,
        interval: str,
        *,
        start_time: int,
        end_time: int,
        page_limit: int = DEFAULT_KLINES_LIMIT,
    ) -> Iterator[MarketBar]:
        """Page through a date range, oldest first, without duplicates.

        Binance returns klines identified by open time and inclusive of
        ``startTime``, so each page resumes one interval past the last open
        time rather than at it.

        Two safeguards, because a duplicated bar would change the dataset hash
        and a non-advancing page would spin forever: a bar whose open time was
        already emitted is skipped, and a page that emits nothing new ends the
        walk.
        """
        step = interval_to_ms(interval)
        cursor = start_time
        highest_emitted: int | None = None
        while cursor <= end_time:
            page = self.klines(
                symbol,
                interval,
                start_time=cursor,
                end_time=end_time,
                limit=page_limit,
            )
            if not page:
                return
            progressed = False
            for bar in page:
                if bar.open_time > end_time:
                    return
                if highest_emitted is not None and bar.open_time <= highest_emitted:
                    continue
                highest_emitted = bar.open_time
                progressed = True
                yield bar
            if not progressed or highest_emitted is None:
                return
            cursor = highest_emitted + step


def _symbols_param(symbols: Sequence[str] | None) -> dict[str, Any]:
    if not symbols:
        return {}
    if len(symbols) == 1:
        return {"symbol": symbols[0]}
    return {"symbols": "[" + ",".join(f'"{s}"' for s in symbols) + "]"}
