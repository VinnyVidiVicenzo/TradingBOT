"""Who a provider *says* an address belongs to, and what we refuse to infer.

An attribution is a claim by one provider at one time. It is stored as such:
the provider, the raw label and raw type exactly as sent (cleaned of control
characters, nothing more), when we recorded it, and a confidence figure **only
if the provider itself supplied one**. We never compute, normalise or invent a
confidence, and we never assume two providers' labels are equally reliable.

Attributions change. The store keeps every distinct claim in order, so "did this
label change later?" is answerable from history instead of being overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .sanitize import clean_text


class EntityType(str, Enum):
    """A deliberately small, closed vocabulary.

    ``OTHER`` means "the provider labelled it and we do not map that label";
    ``UNKNOWN`` means "the provider did not attribute it (or said unknown)".
    Neither is a guess about what the address really is.
    """

    EXCHANGE = "EXCHANGE"
    MARKET_MAKER = "MARKET_MAKER"
    FUND = "FUND"
    CUSTODIAN = "CUSTODIAN"
    TREASURY = "TREASURY"
    INSTITUTION = "INSTITUTION"
    WHALE = "WHALE"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


# Exact-match table. A provider's type string is only mapped when it equals one
# of these tokens after lower-casing and turning spaces/hyphens into
# underscores. Substring matching is refused on purpose: "exchange_wallet_of_x"
# is not evidence of an exchange.
_KNOWN_TOKENS: dict[str, EntityType] = {
    "exchange": EntityType.EXCHANGE,
    "market_maker": EntityType.MARKET_MAKER,
    "fund": EntityType.FUND,
    "custodian": EntityType.CUSTODIAN,
    "treasury": EntityType.TREASURY,
    "protocol_treasury": EntityType.TREASURY,
    "institution": EntityType.INSTITUTION,
    "whale": EntityType.WHALE,
    "unknown": EntityType.UNKNOWN,
    "unknown_wallet": EntityType.UNKNOWN,
}

_SEPARATORS = re.compile(r"[\s\-]+")


def normalize_entity_type(raw: Any) -> EntityType:
    """Map a provider's own type string to the closed vocabulary, or say why not."""
    text = clean_text(raw, max_length=100)
    if text is None:
        return EntityType.UNKNOWN
    token = _SEPARATORS.sub("_", text.lower())
    return _KNOWN_TOKENS.get(token, EntityType.OTHER)


@dataclass(frozen=True, slots=True)
class EntityAttribution:
    """One provider's claim about one address, as of one moment."""

    provider: str
    address: str
    raw_label: str | None
    raw_entity_type: str | None
    entity_type: EntityType
    attributed_at: int
    # How ``attributed_at`` was obtained: "observed" (we recorded it live),
    # "assumed_delay" (derived under a stated policy) or "event_time_proxy"
    # (an import with no receipt time; the event's own time stands in and is
    # NOT a claim about when the provider made the attribution).
    time_basis: str
    blockchain: str | None = None
    # Present only when the provider supplied one, kept exactly as sent.
    provider_confidence: str | None = None
    raw_payload_reference: str | None = None

    def signature(self) -> tuple[str | None, str | None]:
        """What must differ for two attributions to count as a change."""
        return (self.raw_label, self.raw_entity_type)


def build_attribution(
    *,
    provider: str,
    address: Any,
    raw_label: Any,
    raw_entity_type: Any,
    attributed_at: int,
    time_basis: str,
    blockchain: str | None = None,
    provider_confidence: Any = None,
    raw_payload_reference: str | None = None,
) -> EntityAttribution | None:
    """An attribution record, or ``None`` when there is nothing to record.

    No address means nothing to attach a claim to; no label and no type means
    the provider claimed nothing.
    """
    clean_address = clean_text(address, max_length=200)
    label = clean_text(raw_label)
    kind = clean_text(raw_entity_type, max_length=100)
    if clean_address is None or (label is None and kind is None):
        return None
    confidence = clean_text(provider_confidence, max_length=50)
    return EntityAttribution(
        provider=provider,
        address=clean_address,
        raw_label=label,
        raw_entity_type=kind,
        entity_type=normalize_entity_type(kind),
        attributed_at=attributed_at,
        time_basis=time_basis,
        blockchain=clean_text(blockchain, max_length=50),
        provider_confidence=confidence,
        raw_payload_reference=raw_payload_reference,
    )
