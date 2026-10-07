"""Decimal helpers.

Every price, quantity, fee and cash amount in this package is a
:class:`decimal.Decimal`. Floats are never used for money: binary floating
point cannot represent exchange tick sizes exactly, and the resulting rounding
differences would make backtests non-reproducible.
"""

from __future__ import annotations

from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_DOWN,
    ROUND_HALF_UP,
    ROUND_UP,
    localcontext,
)
from typing import Any

# 34 significant digits: enough for 8-decimal crypto quantities multiplied by
# 8-decimal prices without intermediate loss.
PRECISION = 34

ZERO = Decimal(0)
ONE = Decimal(1)
MAX_NUMBER_CHARS = 512
MAX_NUMBER_DIGITS = 128
MAX_NUMBER_EXPONENT = 256


def bounded_decimal(value: Decimal) -> Decimal:
    """Reject nonfinancial magnitudes before integer or fixed-point expansion."""
    if not value.is_finite():
        raise ValueError("number must be finite")
    parts = value.as_tuple()
    if (len(parts.digits) > MAX_NUMBER_DIGITS
            or abs(parts.exponent) > MAX_NUMBER_EXPONENT
            or abs(value.adjusted()) > MAX_NUMBER_EXPONENT):
        raise ValueError("number exceeds supported precision or magnitude")
    return value


def dec(value: Any) -> Decimal:
    """Coerce ``value`` to Decimal.

    Floats are accepted but routed through ``repr`` so that ``dec(0.1)`` is
    ``Decimal("0.1")`` rather than the full binary expansion. Callers should
    still prefer strings.
    """
    if isinstance(value, Decimal):
        return bounded_decimal(value)
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    text = repr(value) if isinstance(value, float) else str(value)
    if len(text) > MAX_NUMBER_CHARS:
        raise ValueError("number token is too long")
    try:
        return bounded_decimal(Decimal(text))
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"cannot convert {value!r} to Decimal") from exc


def mul(a: Decimal, b: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return a * b


def div(a: Decimal, b: Decimal) -> Decimal:
    if b == 0:
        raise ZeroDivisionError("division by zero Decimal")
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return a / b


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round ``value`` DOWN to a multiple of ``step``.

    A ``step`` of zero means unconstrained. Binance.US really does return
    ``"stepSize": "0.00000000"`` on some MARKET_LOT_SIZE filters, so this is a
    live case, not a defensive one.
    """
    if step <= 0:
        return value
    with localcontext() as ctx:
        ctx.prec = PRECISION
        return (value // step) * step


def ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round ``value`` UP to a multiple of ``step``. Zero step is unconstrained."""
    if step <= 0:
        return value
    with localcontext() as ctx:
        ctx.prec = PRECISION
        quotient = value / step
        whole = quotient.to_integral_value(rounding=ROUND_UP)
        return whole * step


def quantize_down(value: Decimal, exponent: Decimal) -> Decimal:
    return value.quantize(exponent, rounding=ROUND_DOWN)


def round_half_up(value: Decimal, places: int) -> Decimal:
    exponent = Decimal(1).scaleb(-places)
    return value.quantize(exponent, rounding=ROUND_HALF_UP)


def step_to_places(step: Decimal) -> int:
    """Number of decimal places implied by a tick or step size."""
    if step <= 0:
        return 8
    exponent = step.normalize().as_tuple().exponent
    if not isinstance(exponent, int):  # pragma: no cover - NaN/Inf specials
        return 8
    return max(0, -exponent)


def format_decimal(value: Decimal) -> str:
    """Canonical, round-trippable string form used for hashing and storage."""
    bounded_decimal(value)
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text
