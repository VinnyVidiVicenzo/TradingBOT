"""The whale-evidence ladder: five levels of evidentiary strength.

    1  a raw large transfer
    2  an attributed transfer (the provider names at least one end)
    3  aggregate repeated flow: many similar transfers in one direction
    4  confirmed large *executed* market activity
    5  an observed market response (a price reaction was measured)

The levels describe how much a piece of information *is*; they do not say how
much it *predicts*. They are never combined into a "whale score", and no
confidence percentage is attached to any of them -- there is nothing here to
derive one from, and inventing one would be false precision. Whether a level
carries any predictive value is an empirical question for the ablation tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Sequence

from ..attribution import EntityType
from ..events import EventClass, EventType, ExternalEvent


class EvidenceLevel(IntEnum):
    RAW_TRANSFER = 1
    ATTRIBUTED_TRANSFER = 2
    AGGREGATE_REPEATED_FLOW = 3
    EXECUTED_MARKET_ACTIVITY = 4
    OBSERVED_MARKET_RESPONSE = 5


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    """A level and the reason for it. Deliberately nothing numeric beyond the level."""

    level: EvidenceLevel
    rationale: str


def classify_event(event: ExternalEvent) -> EvidenceAssessment | None:
    """Level 1, 2 or 4 for a single event; ``None`` for derivatives readings.

    Funding and open interest are market-state readings, not whale activity, so
    they sit outside the ladder.
    """
    if event.event_class is EventClass.DERIVATIVES_STATE:
        return None
    if event.event_class in (EventClass.EXECUTED_TRADE, EventClass.LIQUIDATION):
        return EvidenceAssessment(
            EvidenceLevel.EXECUTED_MARKET_ACTIVITY,
            "the provider reports executed market activity, not a movement between addresses",
        )
    named = any(name for name in (event.source_entity, event.destination_entity))
    typed = any(
        kind not in (None, EntityType.UNKNOWN)
        for kind in (event.source_entity_type, event.destination_entity_type)
    )
    if named or typed:
        return EvidenceAssessment(
            EvidenceLevel.ATTRIBUTED_TRANSFER,
            "the provider attributes at least one end of the transfer (a claim, not a fact)",
        )
    return EvidenceAssessment(
        EvidenceLevel.RAW_TRANSFER, "a large transfer with no attribution on either end"
    )


_DIRECTIONAL = (EventType.EXCHANGE_INFLOW, EventType.EXCHANGE_OUTFLOW)


def classify_flow_cluster(
    events: Sequence[ExternalEvent], *, min_count: int = 3
) -> EvidenceAssessment | None:
    """Level 3 if ``events`` are at least ``min_count`` similar transfers in one direction.

    The caller chooses the window; this only judges whether the events in it are
    the same asset, the same directional type and numerous enough.
    """
    if len(events) < min_count:
        return None
    kinds = {e.event_type for e in events}
    assets = {e.asset for e in events}
    if len(kinds) != 1 or len(assets) != 1 or next(iter(kinds)) not in _DIRECTIONAL:
        return None
    return EvidenceAssessment(
        EvidenceLevel.AGGREGATE_REPEATED_FLOW,
        f"{len(events)} similar {next(iter(kinds)).value} transfers of {next(iter(assets))} in the window",
    )


def observed_response(independent_events: int, min_independent: int = 30) -> EvidenceAssessment | None:
    """Level 5 once an event study has measured a price response on enough independent events.

    This records that a response was *measured*. It says nothing about whether the
    response is real, repeatable or caused by the event.
    """
    if independent_events < min_independent:
        return None
    return EvidenceAssessment(
        EvidenceLevel.OBSERVED_MARKET_RESPONSE,
        f"a price response was measured over {independent_events} independent events",
    )
