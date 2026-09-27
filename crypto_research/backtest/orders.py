"""Hypothetical orders.

Nothing in this module can reach an exchange. An :class:`OrderIntent` is a
strategy saying what it would have done; a :class:`SimulatedOrder` is the
simulator saying what would have happened. Both are records, not requests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Sequence

from ..binanceus.filters import OrderType, RuleViolation
from ..decimals import ZERO, format_decimal
from ..models import CashSource


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"


class RejectionReason(str, Enum):
    """Why a hypothetical order never happened.

    Rejections are first-class results, not errors. A staged-deployment plan
    whose slices are too small to clear the exchange minimum notional is a
    finding about the plan, and it has to survive into the metrics rather
    than being smoothed away.
    """

    DUPLICATE_CLIENT_ORDER_ID = "DUPLICATE_CLIENT_ORDER_ID"
    SELL_NOT_PERMITTED = "SELL_NOT_PERMITTED"
    UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
    NO_REFERENCE_PRICE = "NO_REFERENCE_PRICE"
    STALE_QUOTE = "STALE_QUOTE"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    INSUFFICIENT_HOLDING = "INSUFFICIENT_HOLDING"
    ZERO_QUANTITY_AFTER_ROUNDING = "ZERO_QUANTITY_AFTER_ROUNDING"
    EXCHANGE_FILTER = "EXCHANGE_FILTER"
    LIMIT_NOT_MARKETABLE = "LIMIT_NOT_MARKETABLE"
    MALFORMED_INTENT = "MALFORMED_INTENT"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """What a strategy wants to do at a decision time.

    Exactly one of ``quote_quantity`` (spend this much of the quote asset) or
    ``base_quantity`` (acquire this much of the base asset) must be set.

    ``funding_source`` names the cash bucket the order may draw on. Leaving it
    None means "any bucket", which is fine for exploratory strategies but is
    deliberately *not* what the benchmarks do: Benchmark 1 may only spend
    initial cash and Benchmark 2 may only spend external contributions.
    """

    symbol: str
    side: OrderSide
    order_type: OrderType
    client_order_id: str
    quote_quantity: Decimal | None = None
    base_quantity: Decimal | None = None
    limit_price: Decimal | None = None
    funding_source: CashSource | None = None
    tag: str = ""

    def __post_init__(self) -> None:
        if (self.quote_quantity is None) == (self.base_quantity is None):
            raise ValueError(
                f"{self.client_order_id}: set exactly one of quote_quantity "
                "or base_quantity"
            )
        if self.quote_quantity is not None and self.quote_quantity <= 0:
            raise ValueError(f"{self.client_order_id}: quote_quantity must be positive")
        if self.base_quantity is not None and self.base_quantity <= 0:
            raise ValueError(f"{self.client_order_id}: base_quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError(f"{self.client_order_id}: LIMIT order needs a limit_price")
        if not self.client_order_id:
            raise ValueError("client_order_id is required")


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution slice of a simulated order."""

    sequence: int
    time: int
    price: Decimal
    base_quantity: Decimal
    quote_quantity: Decimal
    fee_amount: Decimal
    fee_asset: str
    is_maker: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "time": self.time,
            "price": format_decimal(self.price),
            "base_quantity": format_decimal(self.base_quantity),
            "quote_quantity": format_decimal(self.quote_quantity),
            "fee_amount": format_decimal(self.fee_amount),
            "fee_asset": self.fee_asset,
            "is_maker": self.is_maker,
        }


@dataclass(slots=True)
class SimulatedOrder:
    """The full record of one hypothetical order, accepted or not."""

    intent: OrderIntent
    decision_time: int
    submit_time: int
    status: OrderStatus
    reference_price: Decimal = ZERO
    effective_price: Decimal = ZERO
    requested_base_quantity: Decimal = ZERO
    filled_base_quantity: Decimal = ZERO
    filled_quote_quantity: Decimal = ZERO
    fees_by_asset: dict[str, Decimal] = field(default_factory=dict)
    fee_in_quote: Decimal = ZERO
    slippage_cost_quote: Decimal = ZERO
    spread_cost_quote: Decimal = ZERO
    fills: list[Fill] = field(default_factory=list)
    rejection_reason: RejectionReason | None = None
    filter_violations: tuple[RuleViolation, ...] = ()
    note: str = ""

    @property
    def accepted(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)

    @property
    def rejected(self) -> bool:
        return self.status is OrderStatus.REJECTED

    def to_dict(self) -> dict[str, object]:
        return {
            "client_order_id": self.intent.client_order_id,
            "symbol": self.intent.symbol,
            "side": self.intent.side.value,
            "order_type": self.intent.order_type.value,
            "tag": self.intent.tag,
            "funding_source": (
                self.intent.funding_source.value if self.intent.funding_source else None
            ),
            "decision_time": self.decision_time,
            "submit_time": self.submit_time,
            "status": self.status.value,
            "reference_price": format_decimal(self.reference_price),
            "effective_price": format_decimal(self.effective_price),
            "requested_base_quantity": format_decimal(self.requested_base_quantity),
            "filled_base_quantity": format_decimal(self.filled_base_quantity),
            "filled_quote_quantity": format_decimal(self.filled_quote_quantity),
            "fee_in_quote": format_decimal(self.fee_in_quote),
            "fees_by_asset": {
                asset: format_decimal(amount)
                for asset, amount in sorted(self.fees_by_asset.items())
            },
            "slippage_cost_quote": format_decimal(self.slippage_cost_quote),
            "spread_cost_quote": format_decimal(self.spread_cost_quote),
            "rejection_reason": (
                self.rejection_reason.value if self.rejection_reason else None
            ),
            "filter_violations": [v.value for v in self.filter_violations],
            "fills": [fill.to_dict() for fill in self.fills],
            "note": self.note,
        }


class ClientOrderIdRegistry:
    """Rejects duplicate client order IDs, exactly as the venue would.

    Binance treats a repeated client order ID as a duplicate and refuses it.
    A strategy that derives IDs from, say, a date will silently collide when
    it fires twice in a day; in a backtest that would show up as double
    buying, so the collision is surfaced here instead.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def __contains__(self, client_order_id: object) -> bool:
        return client_order_id in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def register(self, client_order_id: str) -> bool:
        """True when newly registered, False when it is a duplicate."""
        if client_order_id in self._seen:
            return False
        self._seen.add(client_order_id)
        return True

    def known(self) -> Sequence[str]:
        return tuple(sorted(self._seen))
