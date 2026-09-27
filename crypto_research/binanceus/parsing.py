"""Turn raw Binance.US JSON into the normalized records in crypto_research.models.

Parsing is deliberately strict about two things:

* Numbers arrive from the API as strings and stay strings until they become
  Decimals. They are never routed through float.
* Precision, tick sizes and minimum notionals are read out of the payload.
  Nothing in this module contains a hard-coded value for any symbol.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from ..decimals import dec
from ..errors import FilterParseError
from ..models import (
    EXCHANGE_BINANCE_US,
    ExchangeRuleSnapshot,
    MarketBar,
    MarketQuote,
    Ticker24h,
)

# Index layout of a /api/v3/klines row, confirmed against a live response.
KLINE_OPEN_TIME = 0
KLINE_OPEN = 1
KLINE_HIGH = 2
KLINE_LOW = 3
KLINE_CLOSE = 4
KLINE_VOLUME = 5
KLINE_CLOSE_TIME = 6
KLINE_QUOTE_VOLUME = 7
KLINE_TRADE_COUNT = 8
KLINE_MIN_FIELDS = 9


def parse_kline(
    row: Sequence[Any],
    *,
    symbol: str,
    interval: str,
    received_at: int,
    exchange: str = EXCHANGE_BINANCE_US,
) -> MarketBar:
    if len(row) < KLINE_MIN_FIELDS:
        raise FilterParseError(
            f"kline row for {symbol} has {len(row)} fields, expected at least {KLINE_MIN_FIELDS}"
        )
    return MarketBar(
        exchange=exchange,
        symbol=symbol,
        interval=interval,
        open_time=int(row[KLINE_OPEN_TIME]),
        close_time=int(row[KLINE_CLOSE_TIME]),
        open=dec(row[KLINE_OPEN]),
        high=dec(row[KLINE_HIGH]),
        low=dec(row[KLINE_LOW]),
        close=dec(row[KLINE_CLOSE]),
        volume=dec(row[KLINE_VOLUME]),
        quote_volume=dec(row[KLINE_QUOTE_VOLUME]),
        trade_count=int(row[KLINE_TRADE_COUNT]),
        source_received_at=received_at,
    )


def parse_klines(
    rows: Iterable[Sequence[Any]],
    *,
    symbol: str,
    interval: str,
    received_at: int,
    exchange: str = EXCHANGE_BINANCE_US,
) -> list[MarketBar]:
    return [
        parse_kline(
            row,
            symbol=symbol,
            interval=interval,
            received_at=received_at,
            exchange=exchange,
        )
        for row in rows
    ]


def parse_book_ticker(
    payload: Mapping[str, Any],
    *,
    received_at: int,
    exchange: str = EXCHANGE_BINANCE_US,
) -> MarketQuote:
    return MarketQuote(
        exchange=exchange,
        symbol=str(payload["symbol"]),
        bid=dec(payload["bidPrice"]),
        ask=dec(payload["askPrice"]),
        bid_size=dec(payload["bidQty"]) if payload.get("bidQty") is not None else None,
        ask_size=dec(payload["askQty"]) if payload.get("askQty") is not None else None,
        # /api/v3/ticker/bookTicker does not carry an exchange event time.
        exchange_event_time=_optional_int(payload.get("time")),
        source_received_at=received_at,
    )


def parse_ticker_24h(
    payload: Mapping[str, Any],
    *,
    received_at: int,
    exchange: str = EXCHANGE_BINANCE_US,
) -> Ticker24h:
    return Ticker24h(
        exchange=exchange,
        symbol=str(payload["symbol"]),
        last_price=dec(payload["lastPrice"]),
        open_price=dec(payload["openPrice"]),
        high_price=dec(payload["highPrice"]),
        low_price=dec(payload["lowPrice"]),
        price_change=dec(payload["priceChange"]),
        price_change_percent=dec(payload["priceChangePercent"]),
        weighted_avg_price=dec(payload["weightedAvgPrice"]),
        volume=dec(payload["volume"]),
        quote_volume=dec(payload["quoteVolume"]),
        open_time=int(payload["openTime"]),
        close_time=int(payload["closeTime"]),
        trade_count=_optional_int(payload.get("count")),
        source_received_at=received_at,
    )


# --------------------------------------------------------------------------
# exchangeInfo
# --------------------------------------------------------------------------


def parse_exchange_info(
    payload: Mapping[str, Any],
    *,
    received_at: int,
    exchange: str = EXCHANGE_BINANCE_US,
) -> list[ExchangeRuleSnapshot]:
    """Parse a full exchangeInfo response into one snapshot per symbol."""
    symbols = payload.get("symbols")
    if not isinstance(symbols, list):
        raise FilterParseError("exchangeInfo payload has no 'symbols' array")
    server_time = _optional_int(payload.get("serverTime")) or received_at
    return [
        parse_symbol_rules(
            entry, received_at=received_at, valid_from=server_time, exchange=exchange
        )
        for entry in symbols
    ]


def parse_symbol_rules(
    entry: Mapping[str, Any],
    *,
    received_at: int,
    valid_from: int,
    exchange: str = EXCHANGE_BINANCE_US,
) -> ExchangeRuleSnapshot:
    """Parse one exchangeInfo symbol entry, including its filter list.

    Filters are keyed by ``filterType``. Binance.US currently returns
    MIN_NOTIONAL on some symbols and NOTIONAL on others, so both are read and
    whichever is present wins; if both are present their min/max are merged
    conservatively (tightest constraint).
    """
    try:
        symbol = str(entry["symbol"])
    except KeyError as exc:
        raise FilterParseError("exchangeInfo symbol entry has no 'symbol'") from exc

    by_type = _index_filters(entry.get("filters", []), symbol)

    price_filter = by_type.get("PRICE_FILTER", {})
    lot = by_type.get("LOT_SIZE", {})
    market_lot = by_type.get("MARKET_LOT_SIZE", {})

    min_notional, max_notional, min_to_market, max_to_market, avg_mins = _parse_notional(
        by_type
    )

    return ExchangeRuleSnapshot(
        exchange=exchange,
        symbol=symbol,
        status=str(entry.get("status", "UNKNOWN")),
        base_asset=str(entry.get("baseAsset", "")),
        quote_asset=str(entry.get("quoteAsset", "")),
        price_tick=_optional_dec(price_filter.get("tickSize")),
        min_price=_optional_dec(price_filter.get("minPrice")),
        max_price=_optional_dec(price_filter.get("maxPrice")),
        min_quantity=_optional_dec(lot.get("minQty")),
        max_quantity=_optional_dec(lot.get("maxQty")),
        quantity_step=_optional_dec(lot.get("stepSize")),
        market_min_quantity=_optional_dec(market_lot.get("minQty")),
        market_max_quantity=_optional_dec(market_lot.get("maxQty")),
        market_quantity_step=_optional_dec(market_lot.get("stepSize")),
        min_notional=min_notional,
        max_notional=max_notional,
        min_notional_applies_to_market=min_to_market,
        max_notional_applies_to_market=max_to_market,
        notional_avg_price_mins=avg_mins,
        base_asset_precision=_optional_int(entry.get("baseAssetPrecision")),
        quote_asset_precision=_optional_int(
            entry.get("quoteAssetPrecision", entry.get("quotePrecision"))
        ),
        is_spot_trading_allowed=bool(entry.get("isSpotTradingAllowed", False)),
        order_types=tuple(str(item) for item in entry.get("orderTypes", ())),
        max_num_orders=_optional_int(
            by_type.get("MAX_NUM_ORDERS", {}).get("maxNumOrders")
        ),
        max_num_algo_orders=_optional_int(
            by_type.get("MAX_NUM_ALGO_ORDERS", {}).get("maxNumAlgoOrders")
        ),
        raw_payload=dict(entry),
        valid_from=valid_from,
        source_received_at=received_at,
    )


def _index_filters(
    filters: Iterable[Mapping[str, Any]], symbol: str
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for item in filters:
        if not isinstance(item, Mapping):
            raise FilterParseError(f"{symbol}: filter entry is not an object")
        filter_type = item.get("filterType")
        if not filter_type:
            raise FilterParseError(f"{symbol}: filter entry has no filterType")
        indexed[str(filter_type)] = item
    return indexed


def _parse_notional(
    by_type: Mapping[str, Mapping[str, Any]],
) -> tuple[Decimal | None, Decimal | None, bool, bool, int | None]:
    """Merge MIN_NOTIONAL and NOTIONAL into one pair of bounds.

    Binance.US has been migrating symbols from the older MIN_NOTIONAL filter
    to the newer NOTIONAL filter. As of 2026-09-26 BTCUSD still returns
    MIN_NOTIONAL. Rather than guessing which a symbol uses, both are read and
    the tightest bound wins.
    """
    min_values: list[Decimal] = []
    max_values: list[Decimal] = []
    min_to_market = False
    max_to_market = False
    avg_mins: int | None = None

    legacy = by_type.get("MIN_NOTIONAL")
    if legacy is not None:
        value = _optional_dec(legacy.get("minNotional"))
        if value is not None:
            min_values.append(value)
            min_to_market = bool(legacy.get("applyToMarket", False))
        avg_mins = _optional_int(legacy.get("avgPriceMins")) or avg_mins

    modern = by_type.get("NOTIONAL")
    if modern is not None:
        low = _optional_dec(modern.get("minNotional"))
        high = _optional_dec(modern.get("maxNotional"))
        if low is not None:
            min_values.append(low)
            min_to_market = min_to_market or bool(modern.get("applyMinToMarket", False))
        if high is not None:
            max_values.append(high)
            max_to_market = max_to_market or bool(modern.get("applyMaxToMarket", False))
        avg_mins = _optional_int(modern.get("avgPriceMins")) or avg_mins

    return (
        max(min_values) if min_values else None,
        min(max_values) if max_values else None,
        min_to_market,
        max_to_market,
        avg_mins,
    )


def _optional_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return dec(value)


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
