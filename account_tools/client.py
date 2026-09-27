"""The account reader.

Four questions, nothing else:

* what are my commission rates,
* what do I hold,
* is this key restricted the way I think it is,
* what are the per-symbol trade fees.

Every figure comes back as a Decimal, matching the research side, so a rate
read here and a rate used in a backtest are the same number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Sequence

from crypto_research.decimals import ZERO, dec

from .signing import SignedReadOnlyTransport


@dataclass(frozen=True, slots=True)
class CommissionRates:
    """Standard commission rates reported for the account."""

    maker: Decimal
    taker: Decimal
    buyer: Decimal
    seller: Decimal

    @property
    def is_zero(self) -> bool:
        return self.maker == ZERO and self.taker == ZERO


@dataclass(frozen=True, slots=True)
class Balance:
    asset: str
    free: Decimal
    locked: Decimal

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    """One read of the account, as of one moment."""

    commission: CommissionRates
    balances: tuple[Balance, ...]
    can_trade: bool
    can_withdraw: bool
    can_deposit: bool
    account_type: str
    permissions: tuple[str, ...]
    update_time: int
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def non_zero_balances(self) -> tuple[Balance, ...]:
        return tuple(b for b in self.balances if b.total > ZERO)

    def balance_of(self, asset: str) -> Decimal:
        for balance in self.balances:
            if balance.asset == asset:
                return balance.total
        return ZERO


@dataclass(frozen=True, slots=True)
class SymbolTradeFee:
    symbol: str
    maker: Decimal
    taker: Decimal


class BinanceUSAccountClient:
    """Read-only view of one Binance.US account."""

    def __init__(self, transport: SignedReadOnlyTransport) -> None:
        self._transport = transport

    def account(self) -> AccountSnapshot:
        """Balances plus the account's standard commission rates."""
        payload = self._transport.get("/api/v3/account").payload
        if not isinstance(payload, Mapping):
            raise TypeError("unexpected /api/v3/account payload shape")

        rates = payload.get("commissionRates") or {}
        commission = CommissionRates(
            maker=dec(rates.get("maker", "0")),
            taker=dec(rates.get("taker", "0")),
            buyer=dec(rates.get("buyer", "0")),
            seller=dec(rates.get("seller", "0")),
        )
        balances = tuple(
            Balance(
                asset=str(entry["asset"]).upper(),
                free=dec(entry.get("free", "0")),
                locked=dec(entry.get("locked", "0")),
            )
            for entry in payload.get("balances", ())
        )
        return AccountSnapshot(
            commission=commission,
            balances=tuple(sorted(balances, key=lambda b: b.asset)),
            can_trade=bool(payload.get("canTrade", False)),
            can_withdraw=bool(payload.get("canWithdraw", False)),
            can_deposit=bool(payload.get("canDeposit", False)),
            account_type=str(payload.get("accountType", "")),
            permissions=tuple(str(p) for p in payload.get("permissions", ())),
            update_time=int(payload.get("updateTime", 0)),
            raw=dict(payload),
        )

    def trade_fees(self, symbol: str | None = None) -> list[SymbolTradeFee]:
        """Per-symbol maker/taker fees.

        Per-symbol rates can differ from the account-wide figure when a
        promotion applies to some pairs, which is exactly the case the
        research side needs to know about.
        """
        params = {"symbol": symbol} if symbol else {}
        payload = self._transport.get("/sapi/v1/asset/tradeFee", params).payload
        rows = payload if isinstance(payload, list) else [payload]
        fees: list[SymbolTradeFee] = []
        for row in rows:
            if not isinstance(row, Mapping) or "symbol" not in row:
                continue
            fees.append(
                SymbolTradeFee(
                    symbol=str(row["symbol"]),
                    maker=dec(row.get("makerCommission", row.get("maker", "0"))),
                    taker=dec(row.get("takerCommission", row.get("taker", "0"))),
                )
            )
        return sorted(fees, key=lambda f: f.symbol)

    def symbol_commission(self, symbol: str) -> Mapping[str, Any]:
        """Raw per-symbol commission detail, including any discount."""
        payload = self._transport.get(
            "/api/v3/account/commission", {"symbol": symbol}
        ).payload
        if not isinstance(payload, Mapping):
            raise TypeError("unexpected /api/v3/account/commission payload shape")
        return dict(payload)

    def api_trading_status(self) -> Mapping[str, Any]:
        """The venue's view of this key's trading status."""
        payload = self._transport.get("/sapi/v1/account/apiTradingStatus").payload
        return dict(payload) if isinstance(payload, Mapping) else {}

    def account_status(self) -> Mapping[str, Any]:
        payload = self._transport.get("/sapi/v1/account/status").payload
        return dict(payload) if isinstance(payload, Mapping) else {}


def fees_for_symbols(
    client: BinanceUSAccountClient, symbols: Sequence[str]
) -> dict[str, SymbolTradeFee]:
    """Trade fees restricted to the symbols an experiment actually uses."""
    wanted = {s.upper() for s in symbols}
    return {fee.symbol: fee for fee in client.trade_fees() if fee.symbol in wanted}
