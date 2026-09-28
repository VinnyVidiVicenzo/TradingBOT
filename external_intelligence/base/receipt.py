"""What "the time we received it" means when we did not watch it arrive.

A simulation may only use an event once ``system_received_time <= decision
time``. A live feed has a genuine receipt time: it is the moment we got the
reply. A file of historical events usually does not, and the temptation is to
fall back on the blockchain or trade time, which quietly assumes we could have
acted the instant the event happened.

This module makes that choice explicit and recorded. A policy is *required* by
every import; there is no default. The four policies are:

``none``
    The events get no receipt time at all. They are stored (nothing is
    discarded) but no simulation will ever see them.
``observed_now``
    Receipt time is the moment of ingestion. Correct for a live poll; for an old
    file it is honest but means the events only become usable from today.
``observed_field``
    The record carries our own capture timestamp (from a log we kept at the
    time). Treated as an observation. Records without one get no receipt time.
``assumed_delay``
    Receipt time is estimated as the later of the event time and the provider's
    publish time, plus a stated delay, with the reason written into the event.
    Simulations must opt in to using estimates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..events import ReceiptBasis


class ReceiptMode(str, Enum):
    NONE = "none"
    OBSERVED_NOW = "observed_now"
    OBSERVED_FIELD = "observed_field"
    ASSUMED_DELAY = "assumed_delay"


@dataclass(frozen=True, slots=True)
class ReceiptFields:
    system_received_time: int | None
    estimated_received_time: int | None
    basis: ReceiptBasis
    assumed_delay_ms: int | None
    assumption: str | None


@dataclass(frozen=True, slots=True)
class ReceiptPolicy:
    mode: ReceiptMode
    delay_ms: int | None = None
    rationale: str | None = None

    @classmethod
    def none(cls) -> "ReceiptPolicy":
        return cls(ReceiptMode.NONE)

    @classmethod
    def observed_now(cls) -> "ReceiptPolicy":
        return cls(ReceiptMode.OBSERVED_NOW)

    @classmethod
    def observed_field(cls) -> "ReceiptPolicy":
        return cls(ReceiptMode.OBSERVED_FIELD)

    @classmethod
    def assumed_delay(cls, seconds: float, rationale: str) -> "ReceiptPolicy":
        if seconds < 0:
            raise ValueError("an assumed delay cannot be negative")
        if not rationale or not rationale.strip():
            raise ValueError("an assumed delay must state its reason")
        return cls(ReceiptMode.ASSUMED_DELAY, int(round(seconds * 1000)), rationale.strip())

    def apply(
        self,
        *,
        event_time: int,
        provider_publish_time: int | None,
        record_receipt_ms: int | None,
        ingest_time_ms: int,
    ) -> ReceiptFields:
        unknown = ReceiptFields(None, None, ReceiptBasis.UNKNOWN, None, None)
        if self.mode is ReceiptMode.NONE:
            return unknown
        if self.mode is ReceiptMode.OBSERVED_NOW:
            return ReceiptFields(ingest_time_ms, None, ReceiptBasis.OBSERVED, None, None)
        if self.mode is ReceiptMode.OBSERVED_FIELD:
            if record_receipt_ms is None:
                return unknown
            return ReceiptFields(record_receipt_ms, None, ReceiptBasis.OBSERVED, None, None)
        assert self.delay_ms is not None and self.rationale is not None
        base = max(event_time, provider_publish_time if provider_publish_time is not None else event_time)
        return ReceiptFields(
            None,
            base + self.delay_ms,
            ReceiptBasis.ASSUMED_DELAY,
            self.delay_ms,
            f"{self.rationale} (estimate = max(event time, provider publish time) + "
            f"{self.delay_ms} ms)",
        )

    def describe(self) -> dict[str, object]:
        return {"mode": self.mode.value, "delay_ms": self.delay_ms, "rationale": self.rationale}
