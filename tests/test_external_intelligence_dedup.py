"""Duplicate detection: merge only what a deterministic rule proves, record the rest as ambiguous."""

from __future__ import annotations

import unittest
from decimal import Decimal

from external_intelligence.dedup import (
    RULE_ADDRESS_TIME,
    RULE_HASH,
    RULE_INCONSISTENT_CHAIN,
    RULE_SAME_PROVIDER_REPEAT,
    RULE_UNCONFIRMED_HASH,
    find_duplicates,
    persist_dedup,
)
from external_intelligence.storage import IntelligenceStore

from .ei_helpers import MINUTE, T0, make_event

HASH = "0xabc123"


def transfer(pid, provider="whale_alert", **overrides):
    fields = dict(
        provider=provider, event_time=T0, received=T0 + 1000, amount_asset=Decimal("10"),
        transaction_hash=HASH, source_address="0xsrc", destination_address="0xdst",
        blockchain="ethereum",
    )
    fields.update(overrides)
    return make_event(pid, **fields)


class CrossProviderTests(unittest.TestCase):
    def test_the_same_transaction_reported_by_two_providers_is_one_canonical_event(self) -> None:
        a, b = transfer("1", "whale_alert"), transfer("9", "other", transaction_hash="ABC123")
        result = find_duplicates([a, b])
        self.assertEqual(len(result.groups), 1)
        group = result.groups[0]
        self.assertEqual((group.rule, group.confidence_basis), (RULE_HASH, "transaction_hash"))
        self.assertEqual(set(group.event_ids), {a.event_id, b.event_id})
        self.assertEqual(result.canonical_id_of(a.event_id), result.canonical_id_of(b.event_id))
        self.assertEqual(result.ambiguities, [])

    def test_the_originals_are_preserved_when_a_group_is_recorded(self) -> None:
        a, b = transfer("1", "whale_alert"), transfer("9", "other")
        with IntelligenceStore.open(":memory:") as store:
            store.events.insert(a, inserted_at=1)
            store.events.insert(b, inserted_at=1)
            persist_dedup(store, find_duplicates([a, b]), recorded_at=2)
            persist_dedup(store, find_duplicates([a, b]), recorded_at=3)  # idempotent
            self.assertEqual(store.events.count(), 2)
            self.assertEqual(list(store.canonical.groups().values()), [sorted([a.event_id, b.event_id])])

    def test_the_same_hash_with_a_different_amount_is_related_not_duplicate(self) -> None:
        a = transfer("1", "whale_alert", amount_asset=Decimal("10"))
        b = transfer("2", "other", amount_asset=Decimal("4"))
        result = find_duplicates([a, b])
        self.assertEqual((result.groups, result.ambiguities), ([], []))

    def test_a_hash_match_with_a_missing_amount_is_ambiguous_and_not_merged(self) -> None:
        a = transfer("1", "whale_alert", amount_asset=None, usd=None)
        b = transfer("2", "other")
        result = find_duplicates([a, b])
        self.assertEqual(result.groups, [])
        self.assertEqual({x.rule for x in result.ambiguities}, {RULE_UNCONFIRMED_HASH})

    def test_a_different_destination_under_the_same_hash_is_not_merged(self) -> None:
        a = transfer("1", "whale_alert", destination_address="0xone")
        b = transfer("2", "other", destination_address="0xtwo")
        self.assertEqual(find_duplicates([a, b]).groups, [])

    def test_a_different_chain_or_asset_is_never_merged(self) -> None:
        base = transfer("1", "whale_alert")
        self.assertEqual(find_duplicates([base, transfer("2", "other", blockchain="bitcoin")]).groups, [])
        self.assertEqual(find_duplicates([base, transfer("3", "other", asset="ETH")]).groups, [])


class WithoutAHashTests(unittest.TestCase):
    def pair(self, **overrides):
        a = transfer("1", "whale_alert", transaction_hash=None)
        b = transfer("2", "other", transaction_hash=None, **overrides)
        return find_duplicates([a, b])

    def test_matching_addresses_amount_and_time_merge_with_the_weaker_label(self) -> None:
        result = self.pair(event_time=T0 + 30_000)
        self.assertEqual(len(result.groups), 1)
        self.assertEqual((result.groups[0].rule, result.groups[0].confidence_basis),
                         (RULE_ADDRESS_TIME, "addresses_amount_time"))

    def test_outside_the_time_window_or_amount_tolerance_or_with_a_missing_address_nothing_merges(self) -> None:
        self.assertEqual(self.pair(event_time=T0 + 10 * MINUTE).groups, [])
        self.assertEqual(self.pair(amount_asset=Decimal("10.5")).groups, [])
        self.assertEqual(self.pair(source_address=None).groups, [])
        self.assertEqual(self.pair(amount_asset=None, usd=None).groups, [])

    def test_hex_addresses_compare_case_insensitively_others_do_not(self) -> None:
        self.assertEqual(len(self.pair(source_address="0xSRC", destination_address="0xDST").groups), 1)
        a = transfer("1", "whale_alert", transaction_hash=None, source_address="AbC", destination_address="xYz")
        b = transfer("2", "other", transaction_hash=None, source_address="abc", destination_address="xyz")
        self.assertEqual(find_duplicates([a, b]).groups, [])


class AmbiguityTests(unittest.TestCase):
    def test_the_same_provider_repeating_a_transfer_is_recorded_not_merged(self) -> None:
        a, b = transfer("1", "whale_alert"), transfer("2", "whale_alert")
        result = find_duplicates([a, b])
        self.assertEqual(result.groups, [])
        self.assertEqual({x.rule for x in result.ambiguities}, {RULE_SAME_PROVIDER_REPEAT})

    def test_an_inconsistent_chain_of_matches_is_not_merged(self) -> None:
        # A ~ B by addresses, B ~ C by addresses, but A and C carry different hashes.
        a = transfer("1", "p1", transaction_hash="0xaaa")
        b = transfer("2", "p2", transaction_hash=None, event_time=T0 + 60_000)
        c = transfer("3", "p3", transaction_hash="0xbbb", event_time=T0 + 110_000)
        result = find_duplicates([a, b, c])
        self.assertEqual(result.groups, [])
        self.assertEqual({x.rule for x in result.ambiguities}, {RULE_INCONSISTENT_CHAIN})
        self.assertEqual({x.event_id for x in result.ambiguities}, {a.event_id, b.event_id, c.event_id})

    def test_ambiguities_are_stored_once_however_often_the_check_is_rerun(self) -> None:
        a, b = transfer("1", "whale_alert"), transfer("2", "whale_alert")
        with IntelligenceStore.open(":memory:") as store:
            for when in (1, 2, 3):
                persist_dedup(store, find_duplicates([a, b]), recorded_at=when)
            self.assertEqual(len(store.canonical.ambiguities()), 2)


class ScopeTests(unittest.TestCase):
    def test_executed_trades_are_never_merged_across_providers(self) -> None:
        kwargs = dict(event_type="WHALE_TRADE_BUY", side="BUY", event_time=T0, received=T0 + 1000)
        from external_intelligence.events import Side

        a = make_event("1", provider="coinlobster", **{**kwargs, "side": Side.BUY})
        b = make_event("1", provider="other", **{**kwargs, "side": Side.BUY})
        result = find_duplicates([a, b])
        self.assertEqual((result.groups, result.ambiguities), ([], []))

    def test_the_result_does_not_depend_on_input_order(self) -> None:
        events = [transfer("1", "whale_alert"), transfer("2", "other"), transfer("3", "third", transaction_hash=None)]
        forward = find_duplicates(events)
        backward = find_duplicates(list(reversed(events)))
        self.assertEqual([g.canonical_id for g in forward.groups], [g.canonical_id for g in backward.groups])


if __name__ == "__main__":
    unittest.main()
