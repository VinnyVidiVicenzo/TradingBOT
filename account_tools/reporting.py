"""Render account reads as configuration text for a human to review.

Deliberately text out, not files edited. A measured fee rate entering an
experiment should be a visible, reviewed change to a config file, not
something a tool rewrote while nobody was looking -- the whole registry
design rests on the config being an authored artifact.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

from crypto_research.decimals import ZERO, format_decimal

from .client import AccountSnapshot, SymbolTradeFee

# Assets that are cash for research purposes rather than positions. USD and
# USDT are listed separately and are never netted: they are different assets.
CASH_ASSETS = ("USD", "USDT", "USDC", "BUSD")


def render_fee_block(
    snapshot: AccountSnapshot,
    per_symbol: Mapping[str, SymbolTradeFee],
    *,
    observed_iso: str,
    window: str = "",
) -> str:
    """A ``[costs.fees]`` block carrying real rates and their provenance."""
    commission = snapshot.commission
    note = (
        f"Read from the Binance.US account via /api/v3/account on {observed_iso}. "
        "This is the rate in force at that moment, NOT necessarily the rate that "
        "applied across the backtest window"
    )
    note += f" ({window})." if window else "."
    note += (
        " Fee tiers move with 30-day volume and promotions end, so re-read this "
        "before trusting a result that depends on it."
    )

    lines = [
        "[costs.fees]",
        f'maker_rate = "{format_decimal(commission.maker)}"',
        f'taker_rate = "{format_decimal(commission.taker)}"',
        f'source = "MEASURED -- Binance.US account, {observed_iso}"',
        f'note = "{note}"',
        'commission_asset_policy = "RECEIVED_ASSET"',
    ]

    differing = {
        symbol: fee
        for symbol, fee in sorted(per_symbol.items())
        if fee.maker != commission.maker or fee.taker != commission.taker
    }
    if differing:
        lines.append("")
        lines.append("# These symbols report rates different from the account-wide")
        lines.append("# figure above, so they are pinned individually.")
        for symbol, fee in differing.items():
            lines.extend(
                [
                    "",
                    f"[costs.fees.per_symbol.{symbol}]",
                    f'maker_rate = "{format_decimal(fee.maker)}"',
                    f'taker_rate = "{format_decimal(fee.taker)}"',
                    f'source = "MEASURED -- /sapi/v1/asset/tradeFee, {observed_iso}"',
                ]
            )
    return "\n".join(lines)


def render_holdings_block(
    snapshot: AccountSnapshot,
    *,
    observed_iso: str,
    cash_assets: Sequence[str] = CASH_ASSETS,
) -> str:
    """``[[portfolio.starting_holdings]]`` blocks, plus an ``initial_cash`` line.

    Cash and positions are split here rather than downstream, because the
    research side treats them completely differently: cash is spendable and a
    holding never is. No cost basis is emitted -- the venue does not report
    one, and inferring it would fabricate a number.
    """
    non_zero = snapshot.non_zero_balances()
    cash = {b.asset: b.total for b in non_zero if b.asset in cash_assets}
    positions = [b for b in non_zero if b.asset not in cash_assets]

    lines: list[str] = [
        f"# Read from the Binance.US account on {observed_iso}.",
        "# Balances move. This is a snapshot, not a standing fact.",
        "",
        "[portfolio]",
        'valuation_asset = "USD"',
    ]

    if cash:
        entries = ", ".join(
            f'{asset} = "{format_decimal(amount)}"' for asset, amount in sorted(cash.items())
        )
        lines.append(f"initial_cash = {{ {entries} }}")
        if "USDT" in cash or "USDC" in cash:
            lines.extend(
                [
                    "",
                    "# A stablecoin balance is NOT dollars. Either include its USD pair",
                    "# in [data] symbols so it can be priced, or declare the rate as an",
                    "# explicit assumption -- valuation refuses to guess.",
                    '# assumed_rates = { "USDT->USD" = "1.0" }',
                ]
            )
    else:
        lines.append("initial_cash = { }   # no cash balance found")

    if not positions:
        lines.extend(["", "# No non-cash balances found."])
        return "\n".join(lines)

    for balance in positions:
        lines.extend(
            [
                "",
                "[[portfolio.starting_holdings]]",
                f'asset = "{balance.asset}"',
                f'quantity = "{format_decimal(balance.total)}"',
                f'source = "binance.us account read {observed_iso}"',
                f'as_of = "{observed_iso}"',
                "# cost_basis_per_unit / cost_basis_asset: add only if you actually",
                "# know them. The venue does not report a basis and this tool will",
                "# not invent one.",
            ]
        )
    return "\n".join(lines)


def render_key_check(
    snapshot: AccountSnapshot, trading_status: Mapping[str, object]
) -> str:
    """A plain-language read on how exposed this key is."""
    lines = [
        f"account type      : {snapshot.account_type or '-'}",
        f"permissions       : {', '.join(snapshot.permissions) or '-'}",
        f"account canTrade  : {snapshot.can_trade}",
        f"account canWithdraw: {snapshot.can_withdraw}",
        f"account canDeposit : {snapshot.can_deposit}",
    ]
    data = trading_status.get("data")
    if isinstance(data, Mapping):
        locked = data.get("isLocked")
        lines.append(f"api trading locked: {locked}")

    lines.extend(
        [
            "",
            "IMPORTANT: the flags above describe the ACCOUNT, not this API key.",
            "Binance.US exposes no endpoint that reports a key's own permissions,",
            "so this tool cannot verify your key is read-only. Confirm it in the",
            "API management page: trading disabled, withdrawals disabled, and an",
            "IP allowlist set if your setup allows one.",
            "",
            "What this tool can promise is the other half: it issues GET only,",
            "against five read-only endpoints, and contains no order, cancel,",
            "withdraw, transfer or convert path at all.",
        ]
    )
    return "\n".join(lines)


def summarize_balances(snapshot: AccountSnapshot) -> str:
    rows = snapshot.non_zero_balances()
    if not rows:
        return "no non-zero balances"
    width = max(len(b.asset) for b in rows)
    lines = []
    for balance in rows:
        locked = (
            f"  (locked {format_decimal(balance.locked)})"
            if balance.locked > ZERO
            else ""
        )
        lines.append(f"  {balance.asset:<{width}}  {format_decimal(balance.total)}{locked}")
    return "\n".join(lines)


def _as_decimal(value: object) -> Decimal:
    return value if isinstance(value, Decimal) else ZERO
