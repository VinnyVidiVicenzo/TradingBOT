"""Which features exist, and exactly how each is computed.

A feature is a description of what happened, never a recommendation. Sign
conventions are stated and carry no direction of their own:

* ``exchange_net_flow_usd`` = inflow minus outflow. A positive number means more
  value moved *to* addresses the provider typed as exchanges. It does not mean
  selling, and a negative number does not mean buying.
* ``whale_net_trade_flow`` = executed whale buys minus executed whale sells.
* ``liquidation_imbalance`` = (long - short) / (long + short), between -1 and 1.

Each definition names the *coverage family* it depends on. That is what lets a
window with no events be either a true zero (the feed was complete) or missing
(it was not): see ``base.coverage``.

Nothing here decides how a feature is used. Whether any of them predicts anything
is a question for ``research``, tested against a baseline built from ordinary
market data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from ..base.coverage import (
    FAMILY_DERIVATIVES,
    FAMILY_EXECUTED,
    FAMILY_LIQUIDATIONS,
    FAMILY_TRANSFERS,
)
from ..events import EXECUTED_TRADE_TYPES, HOLDER_TRANSFER_TYPES, EventType
from .windows import parse_window

SCOPE_ASSET = "asset"
SCOPE_STABLECOIN_MARKET = "stablecoin_market"  # issuance across all stablecoins, not one asset

DEFAULT_STATE_MAX_AGE = "24h"
DEFAULT_CHANGE_LOOKBACK = "1h"


class Metric(str, Enum):
    COUNT = "count"
    USD_SUM = "usd_sum"
    USD_NET = "usd_net"  # positive types minus negative types
    RATIO = "ratio"  # positive usd / negative usd
    IMBALANCE = "imbalance"  # (positive - negative) / (positive + negative)
    LATEST_VALUE = "latest_value"  # the most recent reading's ``value``
    LATEST_USD = "latest_usd"  # the most recent reading's ``amount_usd``
    CHANGE_VALUE = "change_value"  # latest ``value`` minus the one known ``lookback`` earlier
    CHANGE_USD = "change_usd"


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: str
    family: str  # transfer | stablecoin | executed | liquidation | derivatives
    coverage_family: str
    scope: str
    metric: Metric
    positive_types: tuple[EventType, ...]
    negative_types: tuple[EventType, ...] = ()
    # Rolling window for flows; the maximum age of a reading for state features.
    window_ms: int = 0
    # For change features: how far back the comparison reading is taken.
    lookback_ms: int = 0
    description: str = ""

    @property
    def all_types(self) -> tuple[EventType, ...]:
        return self.positive_types + self.negative_types


def _label(window: str) -> str:
    """The window as the caller wrote it (``24h`` stays ``24h``), after checking that it parses."""
    parse_window(window)
    return window.strip().lower()


def _def(name: str, family: str, coverage: str, metric: Metric, positive, negative=(),
         window: str | None = None, lookback: str | None = None, scope: str = SCOPE_ASSET,
         description: str = "") -> FeatureDefinition:
    return FeatureDefinition(
        name=name, family=family, coverage_family=coverage, scope=scope, metric=metric,
        positive_types=tuple(positive), negative_types=tuple(negative),
        window_ms=parse_window(window) if window else 0,
        lookback_ms=parse_window(lookback) if lookback else 0,
        description=description,
    )


def transfer_definitions(windows: Sequence[str] = ("1h", "4h")) -> list[FeatureDefinition]:
    out: list[FeatureDefinition] = []
    for window in windows:
        w = _label(window)
        out += [
            _def(f"whale_transfer_count_{w}", "transfer", FAMILY_TRANSFERS, Metric.COUNT,
                 HOLDER_TRANSFER_TYPES, window=w,
                 description="number of large transfers between holders (mints and burns excluded)"),
            _def(f"whale_transfer_usd_{w}", "transfer", FAMILY_TRANSFERS, Metric.USD_SUM,
                 HOLDER_TRANSFER_TYPES, window=w,
                 description="USD value of large transfers between holders"),
            _def(f"exchange_inflow_usd_{w}", "transfer", FAMILY_TRANSFERS, Metric.USD_SUM,
                 [EventType.EXCHANGE_INFLOW], window=w,
                 description="USD moved to addresses the provider typed as exchanges"),
            _def(f"exchange_outflow_usd_{w}", "transfer", FAMILY_TRANSFERS, Metric.USD_SUM,
                 [EventType.EXCHANGE_OUTFLOW], window=w,
                 description="USD moved from addresses the provider typed as exchanges"),
            _def(f"exchange_net_flow_usd_{w}", "transfer", FAMILY_TRANSFERS, Metric.USD_NET,
                 [EventType.EXCHANGE_INFLOW], [EventType.EXCHANGE_OUTFLOW], window=w,
                 description="inflow minus outflow; positive means more moved to exchanges"),
        ]
    return out


def stablecoin_definitions(window: str = "24h") -> list[FeatureDefinition]:
    w = _label(window)
    return [
        _def(f"stablecoin_mint_usd_{w}", "stablecoin", FAMILY_TRANSFERS, Metric.USD_SUM,
             [EventType.STABLECOIN_MINT], window=w, scope=SCOPE_STABLECOIN_MARKET,
             description="USD value newly issued across stablecoins; issuance is not evidence of buying"),
        _def(f"stablecoin_burn_usd_{w}", "stablecoin", FAMILY_TRANSFERS, Metric.USD_SUM,
             [EventType.STABLECOIN_BURN], window=w, scope=SCOPE_STABLECOIN_MARKET,
             description="USD value redeemed across stablecoins; redemption is not evidence of selling"),
    ]


def executed_definitions(windows: Sequence[str] = ("1h",)) -> list[FeatureDefinition]:
    out: list[FeatureDefinition] = []
    for window in windows:
        w = _label(window)
        buy, sell = [EventType.WHALE_TRADE_BUY], [EventType.WHALE_TRADE_SELL]
        out += [
            _def(f"whale_buy_usd_{w}", "executed", FAMILY_EXECUTED, Metric.USD_SUM, buy, window=w,
                 description="USD of executed whale buys"),
            _def(f"whale_sell_usd_{w}", "executed", FAMILY_EXECUTED, Metric.USD_SUM, sell, window=w,
                 description="USD of executed whale sells"),
            _def(f"whale_net_trade_flow_{w}", "executed", FAMILY_EXECUTED, Metric.USD_NET, buy, sell,
                 window=w, description="executed whale buys minus sells"),
            _def(f"whale_buy_sell_ratio_{w}", "executed", FAMILY_EXECUTED, Metric.RATIO, buy, sell,
                 window=w, description="executed whale buys divided by sells; missing when there are no sells"),
            _def(f"large_trade_count_{w}", "executed", FAMILY_EXECUTED, Metric.COUNT,
                 EXECUTED_TRADE_TYPES, window=w, description="number of large executed trades"),
        ]
    return out


def liquidation_definitions(windows: Sequence[str] = ("1h",)) -> list[FeatureDefinition]:
    out: list[FeatureDefinition] = []
    for window in windows:
        w = _label(window)
        long_, short = [EventType.LONG_LIQUIDATION], [EventType.SHORT_LIQUIDATION]
        out += [
            _def(f"long_liquidations_usd_{w}", "liquidation", FAMILY_LIQUIDATIONS, Metric.USD_SUM,
                 long_, window=w, description="USD of long positions force-closed"),
            _def(f"short_liquidations_usd_{w}", "liquidation", FAMILY_LIQUIDATIONS, Metric.USD_SUM,
                 short, window=w, description="USD of short positions force-closed"),
            _def(f"liquidation_imbalance_{w}", "liquidation", FAMILY_LIQUIDATIONS, Metric.IMBALANCE,
                 long_, short, window=w,
                 description="(long - short) / (long + short); missing when there were none"),
        ]
    return out


def derivatives_definitions(
    max_age: str = DEFAULT_STATE_MAX_AGE, change_lookback: str = DEFAULT_CHANGE_LOOKBACK
) -> list[FeatureDefinition]:
    funding, oi = [EventType.FUNDING_UPDATE], [EventType.OPEN_INTEREST_UPDATE]
    return [
        _def("funding_rate", "derivatives", FAMILY_DERIVATIVES, Metric.LATEST_VALUE, funding,
             window=max_age, description="most recent funding reading known at the decision time"),
        _def("funding_rate_change", "derivatives", FAMILY_DERIVATIVES, Metric.CHANGE_VALUE, funding,
             window=max_age, lookback=change_lookback,
             description="funding now minus the reading known one lookback earlier"),
        _def("open_interest", "derivatives", FAMILY_DERIVATIVES, Metric.LATEST_USD, oi,
             window=max_age, description="most recent open-interest reading (USD)"),
        _def("open_interest_change", "derivatives", FAMILY_DERIVATIVES, Metric.CHANGE_USD, oi,
             window=max_age, lookback=change_lookback,
             description="open interest now minus the reading known one lookback earlier"),
    ]


def default_feature_definitions() -> list[FeatureDefinition]:
    """The initial candidate features, exactly as specified."""
    return (
        transfer_definitions(("1h", "4h"))
        + stablecoin_definitions("24h")
        + executed_definitions(("1h",))
        + liquidation_definitions(("1h",))
        + derivatives_definitions()
    )


TRANSFER_FAMILY_NAMES = ("transfer", "stablecoin")
EXECUTED_FAMILY_NAMES = ("executed", "liquidation", "derivatives")


def by_names(definitions: Sequence[FeatureDefinition], names: Sequence[str]) -> list[FeatureDefinition]:
    index = {d.name: d for d in definitions}
    missing = [n for n in names if n not in index]
    if missing:
        raise KeyError(f"unknown feature(s): {missing}")
    return [index[n] for n in names]
