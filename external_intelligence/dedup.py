"""Duplicate detection that never merges what it cannot be sure of.

Two providers can report the same on-chain transfer. Each provider record is kept
as it arrived; a *canonical identity* is a separate, additive claim that several
records describe one transfer, made only when a deterministic rule is satisfied
and nothing contradicts it.

Rules (transfers only -- an executed trade or a funding reading has no
deterministic cross-provider identity and is never merged across providers):

``same_transaction_hash``
    Same asset, same transaction hash (case- and ``0x``-insensitive), equal
    amount, and equal destination address where both give one. Strong.
``same_addresses_amount_time``
    Used only when at least one record has no hash: same asset, same source and
    destination address, amounts within a stated tolerance, event times within a
    stated window. Weaker, and labelled as such.

What is *not* merged, and is recorded as ambiguous instead:

* two records that share a hash but whose amounts differ (a multi-output
  transaction: related, not duplicate);
* a hash match where an amount is missing, so equality cannot be confirmed;
* a record matching more than one candidate, or several records from the *same*
  provider matching one another, or a chain of matches that is not mutually
  consistent.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from .events import EventClass, ExternalEvent
from .storage import IntelligenceStore

RULE_HASH = "same_transaction_hash"
RULE_ADDRESS_TIME = "same_addresses_amount_time"
RULE_UNCONFIRMED_HASH = "hash_match_amount_unconfirmed"
RULE_SAME_PROVIDER_REPEAT = "same_provider_repeat"
RULE_INCONSISTENT_CHAIN = "inconsistent_match_chain"

DEFAULT_TIME_WINDOW_MS = 120_000
DEFAULT_AMOUNT_TOLERANCE = Decimal("0.001")  # 0.1%


@dataclass(frozen=True, slots=True)
class DedupGroup:
    canonical_id: str
    event_ids: tuple[str, ...]
    rule: str
    confidence_basis: str  # "transaction_hash" or "addresses_amount_time"


@dataclass(frozen=True, slots=True)
class Ambiguity:
    event_id: str
    candidate_event_ids: tuple[str, ...]
    rule: str


@dataclass(slots=True)
class DedupResult:
    groups: list[DedupGroup] = field(default_factory=list)
    ambiguities: list[Ambiguity] = field(default_factory=list)

    def canonical_id_of(self, event_id: str) -> str | None:
        for group in self.groups:
            if event_id in group.event_ids:
                return group.canonical_id
        return None


def canonical_id(event_ids: Sequence[str]) -> str:
    return hashlib.sha256("|".join(sorted(event_ids)).encode("utf-8")).hexdigest()[:32]


def find_duplicates(
    events: Sequence[ExternalEvent],
    *,
    time_window_ms: int = DEFAULT_TIME_WINDOW_MS,
    amount_tolerance: Decimal = DEFAULT_AMOUNT_TOLERANCE,
) -> DedupResult:
    transfers = [e for e in events if e.event_class is EventClass.ON_CHAIN_TRANSFER]
    transfers.sort(key=lambda e: (e.asset, e.event_time, e.event_id))
    by_id = {e.event_id: e for e in transfers}

    definite: dict[str, set[str]] = {}
    definite_rule: dict[frozenset[str], str] = {}
    unconfirmed: dict[str, set[str]] = {}
    for i, a in enumerate(transfers):
        for b in transfers[i + 1:]:
            if b.asset != a.asset:
                break
            outcome = _match(a, b, time_window_ms, amount_tolerance)
            if outcome is None:
                continue
            rule, is_definite = outcome
            if is_definite:
                definite.setdefault(a.event_id, set()).add(b.event_id)
                definite.setdefault(b.event_id, set()).add(a.event_id)
                definite_rule[frozenset((a.event_id, b.event_id))] = rule
            else:
                unconfirmed.setdefault(a.event_id, set()).add(b.event_id)
                unconfirmed.setdefault(b.event_id, set()).add(a.event_id)

    result = DedupResult()
    handled: set[str] = set()
    for component in _components(definite):
        members = sorted(component)
        providers = [by_id[m].provider for m in members]
        is_clique = all(
            other in definite.get(m, ()) for m in members for other in members if other != m
        )
        if not is_clique:
            _ambiguous(result, members, RULE_INCONSISTENT_CHAIN)
        elif len(set(providers)) != len(providers):
            _ambiguous(result, members, RULE_SAME_PROVIDER_REPEAT)
        else:
            rules = {definite_rule[frozenset((x, y))] for x in members for y in members if x < y}
            basis = "transaction_hash" if rules == {RULE_HASH} else "addresses_amount_time"
            rule = RULE_HASH if rules == {RULE_HASH} else RULE_ADDRESS_TIME
            result.groups.append(DedupGroup(canonical_id(members), tuple(members), rule, basis))
        handled.update(members)
    for event_id, candidates in sorted(unconfirmed.items()):
        others = tuple(sorted(candidates - {event_id}))
        if others and not (event_id in handled and set(others) <= handled):
            result.ambiguities.append(Ambiguity(event_id, others, RULE_UNCONFIRMED_HASH))
    return result


def persist_dedup(store: IntelligenceStore, result: DedupResult, *, recorded_at: int) -> None:
    """Record groups and ambiguities. The original events are never touched."""
    for group in result.groups:
        store.canonical.record_group(
            group.canonical_id, group.event_ids, rule=group.rule,
            confidence_basis=group.confidence_basis, recorded_at=recorded_at,
        )
    for ambiguity in result.ambiguities:
        store.canonical.record_ambiguity(
            ambiguity.event_id, ambiguity.candidate_event_ids, rule=ambiguity.rule,
            recorded_at=recorded_at,
        )


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------


def _match(
    a: ExternalEvent, b: ExternalEvent, window_ms: int, tolerance: Decimal
) -> tuple[str, bool] | None:
    """``(rule, definite)`` if the two records might be one transfer, else ``None``."""
    if a.asset != b.asset:
        return None
    if a.blockchain is not None and b.blockchain is not None and a.blockchain != b.blockchain:
        return None
    hash_a, hash_b = _norm_hash(a.transaction_hash), _norm_hash(b.transaction_hash)
    if hash_a and hash_b:
        if hash_a != hash_b:
            return None
        if a.amount_asset is None or b.amount_asset is None:
            return RULE_UNCONFIRMED_HASH, False
        if a.amount_asset != b.amount_asset:
            return None  # same transaction, different transfer: related, not duplicate
        if _same_address(a.destination_address, b.destination_address) is False:
            return None
        return RULE_HASH, True
    # At least one record has no hash: fall back to addresses, amount and time.
    for x, y in ((a.source_address, b.source_address), (a.destination_address, b.destination_address)):
        if _same_address(x, y) is not True:
            return None
    if a.amount_asset is None or b.amount_asset is None:
        return None
    if not _within_tolerance(a.amount_asset, b.amount_asset, tolerance):
        return None
    if abs(a.event_time - b.event_time) > window_ms:
        return None
    return RULE_ADDRESS_TIME, True


def _norm_hash(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    return text[2:] if text.startswith("0x") else text


def _same_address(a: str | None, b: str | None) -> bool | None:
    """True/False when both are given, ``None`` when either is missing."""
    if a is None or b is None:
        return None
    if a == b:
        return True
    # Hex addresses are case-insensitive; other encodings (base58, bech32 mixes) are not.
    return a.lower().startswith("0x") and a.lower() == b.lower()


def _within_tolerance(x: Decimal, y: Decimal, tolerance: Decimal) -> bool:
    larger = max(x, y)
    if larger == 0:
        return True
    return abs(x - y) / larger <= tolerance


def _components(adjacency: dict[str, set[str]]) -> list[set[str]]:
    seen: set[str] = set()
    out: list[set[str]] = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack, component = [start], set()
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            stack.extend(adjacency.get(node, ()) - component)
        seen |= component
        out.append(component)
    return out


def _ambiguous(result: DedupResult, members: Sequence[str], rule: str) -> None:
    for member in members:
        others = tuple(m for m in members if m != member)
        result.ambiguities.append(Ambiguity(member, others, rule))
