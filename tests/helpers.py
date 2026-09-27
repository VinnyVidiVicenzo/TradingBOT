"""Fixtures shared by the test suite.

Synthetic data, built to exercise one rule at a time. The one place real
exchange data is used is ``BTCUSD_EXCHANGE_INFO_ENTRY``, which is a verbatim
Binance.US exchangeInfo entry captured on 2026-09-26 -- kept because a
hand-written filter list would only ever test the shape this code already
expects.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, Sequence

from crypto_research.binanceus.filters import SymbolRuleBook
from crypto_research.binanceus.parsing import parse_symbol_rules
from crypto_research.decimals import dec
from crypto_research.models import EXCHANGE_BINANCE_US, MarketBar, MarketQuote
from crypto_research.timeutil import DAY_MS, parse_time

EPOCH_2024 = parse_time("2024-01-01T00:00:00Z")


# Verbatim Binance.US exchangeInfo entry for BTCUSD, captured 2026-09-26.
# Note MIN_NOTIONAL (not NOTIONAL) and MARKET_LOT_SIZE stepSize of zero.
BTCUSD_EXCHANGE_INFO_ENTRY: Mapping[str, Any] = {
    "symbol": "BTCUSD",
    "status": "TRADING",
    "baseAsset": "BTC",
    "baseAssetPrecision": 8,
    "quoteAsset": "USD",
    "quotePrecision": 8,
    "quoteAssetPrecision": 8,
    "orderTypes": [
        "LIMIT", "LIMIT_MAKER", "MARKET", "STOP_LOSS",
        "STOP_LOSS_LIMIT", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT",
    ],
    "icebergAllowed": True,
    "ocoAllowed": True,
    "quoteOrderQtyMarketAllowed": True,
    "isSpotTradingAllowed": True,
    "isMarginTradingAllowed": False,
    "permissionSets": [["SPOT"]],
    "filters": [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01000000",
         "maxPrice": "1000000.00000000", "tickSize": "0.01000000"},
        {"filterType": "PERCENT_PRICE", "multiplierUp": "5",
         "multiplierDown": "0.2", "avgPriceMins": 5},
        {"filterType": "LOT_SIZE", "minQty": "0.00001000",
         "maxQty": "9000.00000000", "stepSize": "0.00001000"},
        {"filterType": "MIN_NOTIONAL", "minNotional": "1.00000000",
         "applyToMarket": True, "avgPriceMins": 5},
        {"filterType": "ICEBERG_PARTS", "limit": 10},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0.00000000",
         "maxQty": "11.07305291", "stepSize": "0.00000000"},
        {"filterType": "MAX_NUM_ORDERS", "maxNumOrders": 200},
        {"filterType": "MAX_NUM_ALGO_ORDERS", "maxNumAlgoOrders": 5},
    ],
}


def make_rule_entry(
    symbol: str,
    *,
    base: str,
    quote: str,
    tick: str = "0.01",
    step: str = "0.00001",
    min_qty: str = "0.00001",
    max_qty: str = "9000",
    min_notional: str | None = "1",
    max_notional: str | None = None,
    market_step: str = "0",
    status: str = "TRADING",
    spot: bool = True,
    use_modern_notional: bool = False,
) -> dict[str, Any]:
    """Build an exchangeInfo-shaped symbol entry."""
    filters: list[dict[str, Any]] = [
        {"filterType": "PRICE_FILTER", "minPrice": "0.01",
         "maxPrice": "1000000", "tickSize": tick},
        {"filterType": "LOT_SIZE", "minQty": min_qty, "maxQty": max_qty, "stepSize": step},
        {"filterType": "MARKET_LOT_SIZE", "minQty": "0",
         "maxQty": "10000", "stepSize": market_step},
    ]
    if use_modern_notional:
        notional: dict[str, Any] = {"filterType": "NOTIONAL", "applyMinToMarket": True,
                                    "applyMaxToMarket": True, "avgPriceMins": 5}
        if min_notional is not None:
            notional["minNotional"] = min_notional
        if max_notional is not None:
            notional["maxNotional"] = max_notional
        filters.append(notional)
    elif min_notional is not None:
        filters.append(
            {"filterType": "MIN_NOTIONAL", "minNotional": min_notional,
             "applyToMarket": True, "avgPriceMins": 5}
        )
    return {
        "symbol": symbol,
        "status": status,
        "baseAsset": base,
        "quoteAsset": quote,
        "baseAssetPrecision": 8,
        "quoteAssetPrecision": 8,
        "orderTypes": ["LIMIT", "LIMIT_MAKER", "MARKET"],
        "isSpotTradingAllowed": spot,
        "filters": filters,
    }


def make_rules(entries: Sequence[Mapping[str, Any]], *, received_at: int = 0) -> SymbolRuleBook:
    return SymbolRuleBook(
        parse_symbol_rules(entry, received_at=received_at, valid_from=received_at)
        for entry in entries
    )


def default_rule_book(received_at: int = 0) -> SymbolRuleBook:
    """BTCUSD, ETHUSD and USDTUSD, all tradable."""
    return make_rules(
        [
            make_rule_entry("BTCUSD", base="BTC", quote="USD"),
            make_rule_entry("ETHUSD", base="ETH", quote="USD", step="0.0001"),
            make_rule_entry("USDTUSD", base="USDT", quote="USD",
                            tick="0.00001", step="1", min_notional="1"),
        ],
        received_at=received_at,
    )


def make_bar(
    symbol: str,
    open_time: int,
    close: str,
    *,
    interval: str = "1d",
    volume: str = "100",
    quote_volume: str = "1000000",
    width_ms: int = DAY_MS,
    received_offset_ms: int = 0,
) -> MarketBar:
    price = dec(close)
    return MarketBar(
        exchange=EXCHANGE_BINANCE_US,
        symbol=symbol,
        interval=interval,
        open_time=open_time,
        close_time=open_time + width_ms - 1,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=dec(volume),
        quote_volume=dec(quote_volume),
        source_received_at=open_time + width_ms - 1 + received_offset_ms,
    )


def make_bars(
    symbol: str,
    closes: Sequence[str],
    *,
    start_time: int = EPOCH_2024,
    interval: str = "1d",
    width_ms: int = DAY_MS,
    quote_volume: str = "1000000",
) -> list[MarketBar]:
    return [
        make_bar(
            symbol,
            start_time + index * width_ms,
            close,
            interval=interval,
            width_ms=width_ms,
            quote_volume=quote_volume,
        )
        for index, close in enumerate(closes)
    ]


def make_quote(
    symbol: str, bid: str, ask: str, *, received_at: int, event_time: int | None = None
) -> MarketQuote:
    return MarketQuote(
        exchange=EXCHANGE_BINANCE_US,
        symbol=symbol,
        bid=dec(bid),
        ask=dec(ask),
        bid_size=dec("1"),
        ask_size=dec("1"),
        exchange_event_time=event_time,
        source_received_at=received_at,
    )


def flat_prices(count: int, price: str) -> list[str]:
    return [price] * count


def d(value: str | int | float) -> Decimal:
    return dec(value)
