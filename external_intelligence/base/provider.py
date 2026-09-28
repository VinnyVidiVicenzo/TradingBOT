"""The contract every provider adapter meets.

An adapter is responsible for: fetching or importing source data, preserving the
raw payload (the ingestion pipeline does this before normalizing anything),
normalizing events, validating schemas, handling missing fields and provider
errors, and staying inside rate limits. It is *not* responsible for -- and has
no means of -- deciding anything about trading. Its only output is typed events
and attribution claims, or a rejection with a reason.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..attribution import EntityAttribution
from ..errors import LiveIngestionNotEnabled
from ..events import ExternalEvent
from .receipt import ReceiptPolicy
from .transport import Fetcher, FetchRequest


@dataclass(frozen=True, slots=True)
class NormalizeContext:
    """What an adapter is told about the record it is normalizing."""

    provider: str
    policy: ReceiptPolicy
    ingest_time_ms: int
    raw_reference: str
    record_index: int


@dataclass(frozen=True, slots=True)
class Rejection:
    reason: str
    detail: str = ""


@dataclass(slots=True)
class NormalizedRecord:
    """The outcome of normalizing one source record."""

    events: list[ExternalEvent] = field(default_factory=list)
    attributions: list[EntityAttribution] = field(default_factory=list)
    rejection: Rejection | None = None
    # Parts of a record that were set aside while the rest was kept (for example
    # one unsupported sub-transaction in a multi-part transaction). Each is
    # quarantined with its reason, so nothing is dropped unrecorded.
    skipped: list[Rejection] = field(default_factory=list)

    @classmethod
    def rejected(cls, reason: str, detail: str = "") -> "NormalizedRecord":
        return cls(rejection=Rejection(reason, detail))


@dataclass(frozen=True, slots=True)
class FetchedPayload:
    text: str
    suffix: str
    latency_ms: int | None = None
    source_ref: str = ""


class Provider(ABC):
    """Base class for provider adapters."""

    name: str = ""
    schema_version: str = "1"
    adapter_version: str = "1"
    # Coverage families this provider can supply (see ``coverage``).
    families: tuple[str, ...] = ()
    # Keys under which a JSON envelope holds its list of records.
    envelope_keys: tuple[str, ...] = ()
    # Why live ingestion is off, stated so a reader is never left to guess.
    live_ingestion_status: str = "not implemented"
    # What was learned about the provider's terms of use.
    terms_note: str = ""

    @abstractmethod
    def normalize_record(self, record: Any, ctx: NormalizeContext) -> NormalizedRecord:
        """Turn one source record into events, or reject it with a reason."""

    def fetch_payload(self, fetcher: Fetcher, request: FetchRequest) -> FetchedPayload:
        """Live retrieval. Off by default: it needs a human's approval first."""
        raise LiveIngestionNotEnabled(f"{self.name}: {self.live_ingestion_status}")

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "adapter_version": self.adapter_version,
            "schema_version": self.schema_version,
            "families": list(self.families),
            "live_ingestion": self.live_ingestion_status,
            "terms_note": self.terms_note,
        }
