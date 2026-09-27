"""Cash, holdings and valuation.

Two separations are load-bearing here and are enforced rather than
documented:

1. **Holdings are not cash.** Starting BTC is a position. It cannot fund a
   purchase. There is no code path that converts a holding into spendable
   cash, because V1 does not sell.

2. **Cash buckets do not merge.** Money you already had (INITIAL) and money
   that arrives later from outside (EXTERNAL_CONTRIBUTION) are tracked
   separately, so a staged-deployment benchmark and a contribution-funded DCA
   benchmark cannot quietly borrow from each other.

A third separation is enforced by simply never aliasing asset codes: USD and
USDT are different assets, and valuing USDT in USD requires a real rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Mapping

from ..decimals import ZERO, div, format_decimal, mul
from ..errors import ValuationError
from ..models import CashLedgerEntry, CashSource, PortfolioHolding


class InsufficientCash(Exception):
    """Raised internally when a debit exceeds a bucket balance."""


@dataclass(slots=True)
class Position:
    """A non-cash holding and what it cost.

    ``cost_by_quote_asset`` is a mapping because a position can be acquired
    with more than one quote asset over time (some USD, some USDT), and
    collapsing those into one number would require an exchange rate that the
    portfolio has no business inventing.
    """

    asset: str
    quantity: Decimal = ZERO
    acquired_quantity: Decimal = ZERO
    cost_by_quote_asset: dict[str, Decimal] = field(default_factory=dict)
    opening_quantity: Decimal = ZERO

    def credit(self, quantity: Decimal, cost: Decimal, quote_asset: str) -> None:
        self.quantity += quantity
        self.acquired_quantity += quantity
        self.cost_by_quote_asset[quote_asset] = (
            self.cost_by_quote_asset.get(quote_asset, ZERO) + cost
        )

    def average_entry_price(self, quote_asset: str) -> Decimal | None:
        """Average price paid per unit, in one quote asset.

        Excludes any opening balance that arrived without an explicit cost
        basis: averaging a known purchase against an unknown one produces a
        number that looks precise and is not.
        """
        if self.acquired_quantity <= 0:
            return None
        cost = self.cost_by_quote_asset.get(quote_asset)
        if cost is None:
            return None
        return div(cost, self.acquired_quantity)


class CashLedger:
    """Spendable balances, split by asset and by source bucket."""

    def __init__(self) -> None:
        self._balances: dict[tuple[str, CashSource], Decimal] = {}
        self._entries: list[CashLedgerEntry] = []

    # -- inspection --------------------------------------------------------

    @property
    def entries(self) -> tuple[CashLedgerEntry, ...]:
        return tuple(self._entries)

    def available(self, asset: str, source: CashSource | None = None) -> Decimal:
        """Spendable balance of ``asset``, optionally restricted to a bucket."""
        if source is not None:
            return self._balances.get((asset, source), ZERO)
        return sum(
            (amount for (code, _), amount in self._balances.items() if code == asset),
            ZERO,
        )

    def assets(self) -> tuple[str, ...]:
        return tuple(sorted({code for code, _ in self._balances}))

    def cumulative_deposits(self, asset: str, source: CashSource | None = None) -> Decimal:
        """Total ever deposited, ignoring anything spent since.

        A strategy needs this to answer "has new money arrived since I last
        looked?". The current balance cannot answer it: a fresh deposit can
        leave the balance at a figure it already held, and rounding dust makes
        balance comparisons unreliable in the other direction. Deposits are
        strictly positive, so this total only ever moves when money arrives.
        """
        return sum(
            (
                entry.amount
                for entry in self._entries
                if entry.asset == asset
                and entry.amount > 0
                and (source is None or entry.source is source)
            ),
            ZERO,
        )

    def totals_by_asset(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for (code, _), amount in self._balances.items():
            out[code] = out.get(code, ZERO) + amount
        return dict(sorted(out.items()))

    def totals_by_bucket(self) -> dict[str, Decimal]:
        return {
            f"{code}:{source.value}": amount
            for (code, source), amount in sorted(
                self._balances.items(), key=lambda kv: (kv[0][0], kv[0][1].value)
            )
        }

    # -- mutation ----------------------------------------------------------

    def deposit(
        self,
        asset: str,
        amount: Decimal,
        source: CashSource,
        *,
        at: int,
        reason: str = "",
    ) -> None:
        if amount <= 0:
            raise ValueError("deposit amount must be positive")
        key = (asset, source)
        self._balances[key] = self._balances.get(key, ZERO) + amount
        self._entries.append(
            CashLedgerEntry(asset=asset, amount=amount, source=source, as_of=at, reason=reason)
        )

    def withdraw(
        self,
        asset: str,
        amount: Decimal,
        source: CashSource | None,
        *,
        at: int,
        reason: str = "",
    ) -> list[tuple[CashSource, Decimal]]:
        """Debit ``amount`` of ``asset``, optionally from one bucket only.

        With ``source=None`` the buckets are drained in a fixed declaration
        order so that a run is reproducible; with a source given, only that
        bucket is touched and an overdraft raises rather than silently
        spilling into another bucket.
        """
        if amount <= 0:
            raise ValueError("withdrawal amount must be positive")
        if self.available(asset, source) < amount:
            raise InsufficientCash(
                f"{format_decimal(amount)} {asset} requested from "
                f"{source.value if source else 'any bucket'}, only "
                f"{format_decimal(self.available(asset, source))} available"
            )

        order = [source] if source is not None else list(CashSource)
        taken: list[tuple[CashSource, Decimal]] = []
        remaining = amount
        for bucket in order:
            if remaining <= 0:
                break
            key = (asset, bucket)
            balance = self._balances.get(key, ZERO)
            if balance <= 0:
                continue
            used = min(balance, remaining)
            self._balances[key] = balance - used
            remaining -= used
            taken.append((bucket, used))
            self._entries.append(
                CashLedgerEntry(
                    asset=asset, amount=-used, source=bucket, as_of=at, reason=reason
                )
            )
        return taken


class Portfolio:
    """Holdings plus cash, with valuation that refuses to guess."""

    def __init__(
        self,
        *,
        starting_holdings: Iterable[PortfolioHolding] = (),
        valuation_asset: str = "USD",
    ) -> None:
        self.valuation_asset = valuation_asset
        self.cash = CashLedger()
        self.positions: dict[str, Position] = {}
        self._opening_cost_basis: dict[str, tuple[Decimal, str]] = {}

        for holding in starting_holdings:
            position = self.positions.setdefault(
                holding.asset, Position(asset=holding.asset)
            )
            position.quantity += holding.quantity
            position.opening_quantity += holding.quantity
            if holding.cost_basis_per_unit is not None and holding.cost_basis_asset:
                # Recorded, but kept out of acquired_quantity so that the
                # average *entry* price of the simulation is not polluted by
                # a basis that predates it.
                self._opening_cost_basis[holding.asset] = (
                    mul(holding.cost_basis_per_unit, holding.quantity),
                    holding.cost_basis_asset,
                )

    # -- holdings ----------------------------------------------------------

    def quantity(self, asset: str) -> Decimal:
        position = self.positions.get(asset)
        return position.quantity if position else ZERO

    def credit_asset(
        self, asset: str, quantity: Decimal, *, cost: Decimal, quote_asset: str
    ) -> None:
        position = self.positions.setdefault(asset, Position(asset=asset))
        position.credit(quantity, cost, quote_asset)

    def opening_cost_basis(self, asset: str) -> tuple[Decimal, str] | None:
        return self._opening_cost_basis.get(asset)

    def average_entry_price(self, asset: str, quote_asset: str) -> Decimal | None:
        position = self.positions.get(asset)
        return position.average_entry_price(quote_asset) if position else None

    def non_cash_assets(self) -> tuple[str, ...]:
        return tuple(
            sorted(asset for asset, pos in self.positions.items() if pos.quantity > 0)
        )

    # -- valuation ---------------------------------------------------------

    def value(self, rates: "RateView") -> Decimal:
        """Total portfolio value in the valuation asset.

        Raises ValuationError when any component cannot be priced. That is a
        deliberate choice: a portfolio that silently values an unpriceable
        asset at zero, or a stablecoin at par, reports a number that is not
        the truth.
        """
        total = ZERO
        for asset, position in sorted(self.positions.items()):
            if position.quantity == 0:
                continue
            total += mul(position.quantity, rates.rate(asset, self.valuation_asset))
        for asset, amount in self.cash.totals_by_asset().items():
            if amount == 0:
                continue
            total += mul(amount, rates.rate(asset, self.valuation_asset))
        return total

    def cash_value(self, rates: "RateView") -> Decimal:
        total = ZERO
        for asset, amount in self.cash.totals_by_asset().items():
            if amount == 0:
                continue
            total += mul(amount, rates.rate(asset, self.valuation_asset))
        return total

    def snapshot(self, rates: "RateView | None" = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "valuation_asset": self.valuation_asset,
            "holdings": {
                asset: format_decimal(pos.quantity)
                for asset, pos in sorted(self.positions.items())
                if pos.quantity != 0
            },
            "cash_by_asset": {
                asset: format_decimal(amount)
                for asset, amount in self.cash.totals_by_asset().items()
            },
            "cash_by_bucket": {
                key: format_decimal(amount)
                for key, amount in self.cash.totals_by_bucket().items()
            },
        }
        if rates is not None:
            payload["total_value"] = format_decimal(self.value(rates))
        return payload


class RateView:
    """Converts asset amounts into the valuation asset.

    Resolution order, and nothing else:

    1. identity, when the asset *is* the valuation asset;
    2. a live pair price visible at the current simulated time;
    3. an explicitly configured assumed rate, which is recorded in the run
       record as an assumption.

    There is no step 4. In particular USDT is not treated as USD. Binance.US
    lists USDTUSD, so the honest thing is to price it, and if that price is
    not in the dataset the run fails loudly.
    """

    def __init__(
        self,
        prices: Mapping[str, Decimal],
        *,
        valuation_asset: str,
        assumed_rates: Mapping[tuple[str, str], Decimal] | None = None,
    ) -> None:
        self._prices = prices
        self._valuation_asset = valuation_asset
        self._assumed = dict(assumed_rates or {})
        self.used_assumptions: set[tuple[str, str]] = set()

    def rate(self, asset: str, valuation_asset: str | None = None) -> Decimal:
        target = valuation_asset or self._valuation_asset
        if asset == target:
            return Decimal(1)

        direct = self._prices.get(f"{asset}{target}")
        if direct is not None and direct > 0:
            return direct

        inverse = self._prices.get(f"{target}{asset}")
        if inverse is not None and inverse > 0:
            return div(Decimal(1), inverse)

        assumed = self._assumed.get((asset, target))
        if assumed is not None:
            self.used_assumptions.add((asset, target))
            return assumed

        raise ValuationError(
            f"cannot value {asset} in {target}: no {asset}{target} price is visible "
            f"and no assumed rate was configured. Add the pair to the dataset or "
            f"declare the rate as an explicit assumption -- {asset} will not be "
            f"assumed equal to {target}."
        )
