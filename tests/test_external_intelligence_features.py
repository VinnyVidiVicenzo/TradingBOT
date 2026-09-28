"""Features: point in time, missing stays missing, deterministic, lagged-only normalisation."""

from __future__ import annotations

import json
import random
import statistics
import unittest
from decimal import Decimal

from external_intelligence.base.coverage import CoverageIndex, CoverageInterval
from external_intelligence.base.health import HealthStatus
from external_intelligence.events import Side
from external_intelligence.features import (
    FeaturePipeline,
    FeatureStatus,
    InMemoryEventSource,
    RepositoryEventSource,
    default_feature_definitions,
    format_window,
    parse_window,
    transfer_definitions,
)
from external_intelligence.features.definitions import by_names
from external_intelligence.features.normalization import (
    Method,
    NormalizedFeatureBuilder,
    NormalizedFeatureSpec,
    Observation,
    asset_relative,
    build_series,
    default_normalized_specs,
    ratio_to_trailing_mean,
    ratio_to_trailing_median,
    rolling_percentile,
    rolling_zscore,
    share_of_trailing_total,
    trailing_values,
)
from external_intelligence.storage import IntelligenceStore

from .ei_helpers import HOUR, MINUTE, T0, full_coverage, make_event

D = T0 + 100 * HOUR  # the decision time used throughout


def pipeline(events, *, coverage=None, **kwargs) -> FeaturePipeline:
    return FeaturePipeline(
        InMemoryEventSource(events), coverage if coverage is not None else full_coverage(T0, D + 100 * HOUR), **kwargs
    )


def feature(events, name, *, asset="BTC", at=D, **kwargs):
    return pipeline(events, **kwargs).compute(asset, at, names=[name]).get(name)


def inflow(pid, usd="1000000", *, ago=10 * MINUTE, lag=MINUTE, **kwargs):
    return make_event(pid, event_time=D - ago, received=D - ago + lag, usd=usd, **kwargs)


class PointInTimeFeatureTests(unittest.TestCase):
    def test_an_event_counts_from_its_receipt_time_not_its_event_time(self) -> None:
        late = make_event(1, event_time=D - 10 * MINUTE, received=D + 5 * MINUTE)
        at_decision = feature([late], "exchange_inflow_usd_1h")
        self.assertEqual((at_decision.value, at_decision.n_events), (Decimal(0), 0))
        later = feature([late], "exchange_inflow_usd_1h", at=D + 5 * MINUTE)
        self.assertEqual(later.value, Decimal("1000000"))

    def test_extra_delay_moves_the_availability_cutoff_earlier(self) -> None:
        event = inflow(1, lag=MINUTE)  # received 9 minutes before D
        self.assertEqual(feature([event], "exchange_inflow_usd_1h").value, Decimal("1000000"))
        slowed = feature([event], "exchange_inflow_usd_1h", extra_delay_ms=10 * MINUTE)
        self.assertEqual(slowed.value, Decimal(0))
        with self.assertRaises(ValueError):
            pipeline([], extra_delay_ms=-1)

    def test_estimated_receipts_are_used_only_when_explicitly_allowed(self) -> None:
        event = make_event(1, event_time=D - 30 * MINUTE, estimated=(D - 20 * MINUTE, 10 * MINUTE, "assumed"))
        self.assertEqual(feature([event], "exchange_inflow_usd_1h").value, Decimal(0))
        allowed = feature([event], "exchange_inflow_usd_1h", allow_estimated=True)
        self.assertEqual(allowed.value, Decimal("1000000"))

    def test_an_event_with_no_receipt_time_is_never_used(self) -> None:
        event = make_event(1, event_time=D - 30 * MINUTE)
        self.assertEqual(feature([event], "exchange_inflow_usd_1h", allow_estimated=True).value, Decimal(0))

    def test_an_event_from_the_future_is_never_used(self) -> None:
        event = make_event(1, event_time=D + 5 * MINUTE, received=D + 5 * MINUTE)
        self.assertEqual(feature([event], "exchange_inflow_usd_1h").value, Decimal(0))

    def test_the_window_is_open_at_the_start_and_closed_at_the_decision_time(self) -> None:
        edge_old = make_event(1, event_time=D - HOUR, received=D - HOUR + 1)
        edge_new = make_event(2, event_time=D, received=D)
        value = feature([edge_old, edge_new], "exchange_inflow_usd_1h")
        self.assertEqual((value.value, value.n_events), (Decimal("1000000"), 1))

    def test_the_store_and_an_in_memory_list_give_identical_features(self) -> None:
        events = [inflow(1), inflow(2, "250000", ago=20 * MINUTE),
                  make_event(3, event_time=D - MINUTE, received=D + MINUTE)]
        with IntelligenceStore.open(":memory:") as store:
            for event in events:
                store.events.insert(event, inserted_at=0)
            from_store = FeaturePipeline(RepositoryEventSource(store.events), full_coverage(T0, D + HOUR))
            from_list = pipeline(events)
            self.assertEqual(from_store.compute("BTC", D).to_dict(), from_list.compute("BTC", D).to_dict())


class MissingVersusZeroTests(unittest.TestCase):
    def test_a_covered_window_with_nothing_in_it_is_a_true_zero(self) -> None:
        value = feature([], "exchange_inflow_usd_1h")
        self.assertEqual((value.value, value.status), (Decimal(0), FeatureStatus.OK))
        self.assertEqual(feature([], "whale_transfer_count_1h").value, 0)

    def test_an_uncovered_window_is_missing_never_zero(self) -> None:
        for events in ([], [inflow(1)]):
            value = feature(events, "exchange_inflow_usd_1h", coverage=CoverageIndex())
            self.assertIsNone(value.value)
            self.assertEqual(value.status, FeatureStatus.MISSING_NO_COVERAGE)
        self.assertIn("lower bound", feature([inflow(1)], "exchange_inflow_usd_1h", coverage=CoverageIndex()).detail)

    def test_a_window_resting_on_estimated_receipts_is_missing_not_zero_unless_estimates_are_allowed(self) -> None:
        coverage = CoverageIndex([CoverageInterval("whale_alert", "transfers", T0, D + HOUR, "d", None, "estimated")])
        event = make_event(1, event_time=D - 30 * MINUTE, estimated=(D - 20 * MINUTE, 10 * MINUTE, "assumed"))
        refused = feature([event], "exchange_inflow_usd_1h", coverage=coverage)
        self.assertEqual((refused.value, refused.status), (None, FeatureStatus.MISSING_NO_COVERAGE))
        self.assertIn("estimated receipt times", refused.detail)
        allowed = feature([event], "exchange_inflow_usd_1h", coverage=coverage, allow_estimated=True)
        self.assertEqual(allowed.value, Decimal("1000000"))

    def test_a_window_only_known_after_the_decision_is_missing_before_it_and_usable_after(self) -> None:
        coverage = CoverageIndex([CoverageInterval("whale_alert", "transfers", T0, D + 5 * HOUR, "d", None, "observed", D + HOUR)])
        before = feature([], "exchange_inflow_usd_1h", coverage=coverage)
        self.assertEqual((before.value, before.status), (None, FeatureStatus.MISSING_NO_COVERAGE))
        after = feature([], "exchange_inflow_usd_1h", coverage=coverage, at=D + 2 * HOUR)
        self.assertEqual((after.value, after.status), (Decimal(0), FeatureStatus.OK))

    def test_extra_delay_moves_the_moment_from_which_a_window_is_known_as_well(self) -> None:
        coverage = CoverageIndex([CoverageInterval("whale_alert", "transfers", T0, D + 5 * HOUR, "d", None, "observed", D)])
        self.assertEqual(feature([], "exchange_inflow_usd_1h", coverage=coverage).value, Decimal(0))
        slowed = feature([], "exchange_inflow_usd_1h", coverage=coverage, extra_delay_ms=MINUTE)
        self.assertIsNone(slowed.value)

    def test_a_window_declared_for_one_asset_leaves_the_others_and_market_wide_features_missing(self) -> None:
        btc_only = CoverageIndex([CoverageInterval("whale_alert", "transfers", T0, D + HOUR, "d", assets=("BTC",))])
        covered = feature([], "exchange_inflow_usd_1h", coverage=btc_only, asset="BTC")
        self.assertEqual((covered.value, covered.status), (Decimal(0), FeatureStatus.OK))
        other = feature([], "exchange_inflow_usd_1h", coverage=btc_only, asset="ETH")
        self.assertEqual((other.value, other.status), (None, FeatureStatus.MISSING_NO_COVERAGE))
        self.assertIn("for this asset", other.detail)
        market_wide = feature([], "stablecoin_mint_usd_24h", coverage=btc_only, asset="BTC")
        self.assertEqual((market_wide.value, market_wide.status), (None, FeatureStatus.MISSING_NO_COVERAGE))

    def test_partial_coverage_of_the_window_is_not_enough(self) -> None:
        half = CoverageIndex([CoverageInterval("whale_alert", "transfers", D - 30 * MINUTE, D + HOUR, "declared")])
        self.assertIsNone(feature([], "exchange_inflow_usd_1h", coverage=half).value)

    def test_one_event_without_an_amount_makes_the_sum_missing_but_not_the_count(self) -> None:
        events = [inflow(1), inflow(2, usd=None, ago=20 * MINUTE)]
        total = feature(events, "exchange_inflow_usd_1h")
        self.assertIsNone(total.value)
        self.assertEqual(total.status, FeatureStatus.MISSING_PARTIAL_AMOUNTS)
        self.assertEqual(feature(events, "whale_transfer_count_1h").value, 2)

    def test_undefined_ratios_are_missing_not_zero_or_infinite(self) -> None:
        buys_only = [make_event(1, provider="coinlobster", event_type="WHALE_TRADE_BUY", side=Side.BUY,
                                event_time=D - MINUTE, received=D, usd="500000")]
        ratio = feature(buys_only, "whale_buy_sell_ratio_1h")
        self.assertEqual((ratio.value, ratio.status), (None, FeatureStatus.MISSING_UNDEFINED))
        self.assertEqual(feature(buys_only, "whale_net_trade_flow_1h").value, Decimal("500000"))
        self.assertIsNone(feature([], "liquidation_imbalance_1h").value)

    def test_ratios_and_imbalance_when_they_are_defined(self) -> None:
        trades = [
            make_event(1, provider="coinlobster", event_type="WHALE_TRADE_BUY", side=Side.BUY,
                       event_time=D - 5 * MINUTE, received=D, usd="600000"),
            make_event(2, provider="coinlobster", event_type="WHALE_TRADE_SELL", side=Side.SELL,
                       event_time=D - 4 * MINUTE, received=D, usd="200000"),
            make_event(3, provider="coinlobster", event_type="LONG_LIQUIDATION",
                       event_time=D - 3 * MINUTE, received=D, usd="900000"),
            make_event(4, provider="coinlobster", event_type="SHORT_LIQUIDATION",
                       event_time=D - 2 * MINUTE, received=D, usd="100000"),
        ]
        self.assertEqual(feature(trades, "whale_buy_sell_ratio_1h").value, Decimal(3))
        self.assertEqual(feature(trades, "liquidation_imbalance_1h").value, Decimal("0.8"))

    def test_no_provider_configured_is_a_stated_reason(self) -> None:
        value = feature([inflow(1)], "exchange_inflow_usd_1h", providers={})
        self.assertEqual((value.value, value.status), (None, FeatureStatus.MISSING_NO_PROVIDER))

    def test_missing_values_serialise_as_null_not_zero(self) -> None:
        snapshot = pipeline([], coverage=CoverageIndex()).compute("BTC", D)
        encoded = json.loads(json.dumps(snapshot.to_dict()))
        self.assertTrue(all(v["value"] is None for v in encoded["values"].values()))
        self.assertTrue(all(v["status"].startswith("MISSING") for v in encoded["values"].values()))


class ClassSeparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.events = [
            inflow(1, "1000000"),
            make_event(2, provider="coinlobster", event_type="WHALE_TRADE_BUY", side=Side.BUY,
                       event_time=D - 5 * MINUTE, received=D - 4 * MINUTE, usd="7000000"),
        ]

    def test_an_executed_trade_does_not_change_transfer_features_or_the_reverse(self) -> None:
        transfer_only = pipeline(self.events[:1]).compute("BTC", D)
        both = pipeline(self.events).compute("BTC", D)
        for name in ("exchange_inflow_usd_1h", "whale_transfer_usd_1h", "whale_transfer_count_1h"):
            self.assertEqual(transfer_only.get(name).value, both.get(name).value, name)
        self.assertEqual(both.get("whale_buy_usd_1h").value, Decimal("7000000"))
        self.assertEqual(pipeline(self.events[:1]).compute("BTC", D).get("whale_buy_usd_1h").value, Decimal(0))

    def test_minted_and_burned_tokens_are_not_transfers_between_holders(self) -> None:
        mint = make_event(3, asset="USDT", event_type="STABLECOIN_MINT", event_time=D - MINUTE, received=D, usd="50000000")
        self.assertEqual(feature([mint], "whale_transfer_usd_1h", asset="USDT").value, Decimal(0))
        self.assertEqual(feature([mint], "stablecoin_mint_usd_24h", asset="BTC").value, Decimal("50000000"))

    def test_stablecoin_issuance_is_market_wide_and_never_a_buy_or_sell_reading(self) -> None:
        events = [
            make_event(1, asset="USDT", event_type="STABLECOIN_MINT", event_time=D - HOUR, received=D - HOUR + 1, usd="10000000"),
            make_event(2, asset="USDC", event_type="STABLECOIN_MINT", event_time=D - HOUR, received=D - HOUR + 1, usd="5000000"),
            make_event(3, asset="USDT", event_type="STABLECOIN_BURN", event_time=D - HOUR, received=D - HOUR + 1, usd="2000000"),
        ]
        snapshot = pipeline(events).compute("BTC", D)
        self.assertEqual(snapshot.get("stablecoin_mint_usd_24h").value, Decimal("15000000"))
        self.assertEqual(snapshot.get("stablecoin_burn_usd_24h").value, Decimal("2000000"))
        for name in ("whale_buy_usd_1h", "whale_net_trade_flow_1h", "exchange_net_flow_usd_1h"):
            self.assertEqual(snapshot.get(name).value, Decimal(0), name)

    def test_usd_a_stablecoin_and_a_coin_are_separate_assets(self) -> None:
        events = [
            inflow(1, "3000000", asset="USDT"),
            inflow(2, "1000000", asset="BTC"),
            inflow(3, "500000", asset="USD"),
        ]
        by_asset = {a: feature(events, "exchange_inflow_usd_1h", asset=a).value for a in ("BTC", "USDT", "USD", "ETH")}
        self.assertEqual(by_asset, {"BTC": Decimal("1000000"), "USDT": Decimal("3000000"),
                                    "USD": Decimal("500000"), "ETH": Decimal(0)})

    def test_net_flow_is_inflow_minus_outflow_and_carries_no_direction_of_its_own(self) -> None:
        events = [inflow(1, "900000"), inflow(2, "400000", ago=20 * MINUTE, event_type="EXCHANGE_OUTFLOW")]
        self.assertEqual(feature(events, "exchange_net_flow_usd_1h").value, Decimal("500000"))
        definition = {d.name: d for d in default_feature_definitions()}["exchange_net_flow_usd_1h"]
        self.assertIn("positive means more moved to exchanges", definition.description)
        self.assertNotIn("sell", definition.description.lower())


class StateFeatureTests(unittest.TestCase):
    def funding(self, pid, value, *, ago, received_ago=None):
        received = D - (ago if received_ago is None else received_ago)
        return make_event(pid, provider="coinlobster", event_type="FUNDING_UPDATE", usd=None,
                          value=Decimal(value), value_unit="funding_rate_per_interval",
                          event_time=D - ago, received=received + 1000)

    def test_the_latest_reading_is_used_and_an_old_one_is_stale(self) -> None:
        events = [self.funding(1, "0.0001", ago=90 * MINUTE), self.funding(2, "0.0003", ago=30 * MINUTE)]
        self.assertEqual(feature(events, "funding_rate").value, Decimal("0.0003"))
        old = feature([self.funding(3, "0.0001", ago=30 * HOUR)], "funding_rate")
        self.assertEqual((old.value, old.status), (None, FeatureStatus.MISSING_STALE))

    def test_the_change_compares_with_the_reading_known_one_lookback_earlier(self) -> None:
        events = [self.funding(1, "0.0001", ago=90 * MINUTE), self.funding(2, "0.0003", ago=30 * MINUTE)]
        self.assertEqual(feature(events, "funding_rate_change").value, Decimal("0.0002"))

    def test_an_earlier_reading_that_was_reported_late_was_not_known_then(self) -> None:
        # Happened 90 minutes ago but only reached us 20 minutes ago: at the time
        # one lookback earlier we could not have compared against it.
        events = [self.funding(1, "0.0001", ago=90 * MINUTE, received_ago=20 * MINUTE),
                  self.funding(2, "0.0003", ago=30 * MINUTE)]
        change = feature(events, "funding_rate_change")
        self.assertEqual((change.value, change.status), (None, FeatureStatus.MISSING_STALE))

    def test_open_interest_reads_the_latest_usd_amount(self) -> None:
        oi = make_event(1, provider="coinlobster", event_type="OPEN_INTEREST_UPDATE",
                        event_time=D - 5 * MINUTE, received=D - 4 * MINUTE, usd="5000000000")
        self.assertEqual(feature([oi], "open_interest").value, Decimal("5000000000"))


class HealthGateTests(unittest.TestCase):
    def test_an_unhealthy_provider_turns_its_features_missing_and_leaves_the_others(self) -> None:
        def gate(provider: str, at: int) -> HealthStatus:
            return HealthStatus(healthy=provider != "whale_alert", reasons=("consecutive_failures>=3",))

        events = [inflow(1), make_event(2, provider="coinlobster", event_type="LARGE_EXECUTED_TRADE",
                                        event_time=D - MINUTE, received=D, usd="900000")]
        snapshot = pipeline(events, health_gate=gate).compute("BTC", D)
        blocked = snapshot.get("exchange_inflow_usd_1h")
        self.assertEqual((blocked.value, blocked.status), (None, FeatureStatus.MISSING_PROVIDER_UNHEALTHY))
        self.assertIn("consecutive_failures", blocked.detail)
        self.assertEqual(snapshot.get("large_trade_count_1h").value, 1)


class DeterminismTests(unittest.TestCase):
    def events(self):
        return [inflow(i, str(100_000 * (i + 1)), ago=(i + 1) * 3 * MINUTE) for i in range(8)]

    def test_the_same_inputs_give_identical_features_and_hash(self) -> None:
        first = pipeline(self.events()).compute("BTC", D)
        second = pipeline(self.events()).compute("BTC", D)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.input_hash, second.input_hash)

    def test_the_order_events_arrive_in_does_not_matter(self) -> None:
        shuffled = self.events()
        random.Random(7).shuffle(shuffled)
        self.assertEqual(pipeline(shuffled).compute("BTC", D).to_dict(),
                         pipeline(self.events()).compute("BTC", D).to_dict())

    def test_an_event_that_cannot_have_been_used_does_not_change_the_hash(self) -> None:
        base = pipeline(self.events()).compute("BTC", D)
        extra = make_event(99, event_time=D + HOUR, received=D + HOUR)
        self.assertEqual(pipeline(self.events() + [extra]).compute("BTC", D).input_hash, base.input_hash)

    def test_a_different_event_set_or_setting_changes_the_hash(self) -> None:
        base = pipeline(self.events()).compute("BTC", D)
        self.assertNotEqual(pipeline(self.events()[:-1]).compute("BTC", D).input_hash, base.input_hash)
        self.assertNotEqual(pipeline(self.events(), allow_estimated=True).compute("BTC", D).input_hash, base.input_hash)

    def test_asking_for_an_unknown_feature_is_an_error_not_a_silent_gap(self) -> None:
        with self.assertRaises(KeyError):
            pipeline([]).compute("BTC", D, names=["exchange_inflow_usd_1h", "no_such_feature"])
        with self.assertRaises(KeyError):
            by_names(default_feature_definitions(), ["nope"])


class DefinitionTests(unittest.TestCase):
    def test_the_default_set_is_named_described_and_unique(self) -> None:
        definitions = default_feature_definitions()
        names = [d.name for d in definitions]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(d.description for d in definitions))
        for expected in ("whale_transfer_count_1h", "exchange_net_flow_usd_4h", "stablecoin_mint_usd_24h",
                         "whale_net_trade_flow_1h", "liquidation_imbalance_1h", "funding_rate_change",
                         "open_interest_change", "large_trade_count_1h"):
            self.assertIn(expected, names)

    def test_no_feature_name_or_description_recommends_an_action(self) -> None:
        for d in default_feature_definitions():
            text = f"{d.name} {d.description}".lower()
            for word in ("should", "recommend", "bullish", "bearish", "signal", "score"):
                self.assertNotIn(word, text, d.name)

    def test_every_window_the_spec_lists_parses_and_round_trips(self) -> None:
        self.assertEqual([parse_window(w) for w in ("15m", "1h", "4h", "24h", "7d")],
                         [15 * MINUTE, HOUR, 4 * HOUR, 24 * HOUR, 7 * 24 * HOUR])
        for text in ("15m", "1h", "4h", "1d", "1w"):
            self.assertEqual(format_window(parse_window(text)), text)
        for bad in ("", "0h", "5x", "h", "-1h", "1.5h"):
            with self.assertRaises(ValueError, msg=bad):
                parse_window(bad)
        names = [d.name for d in transfer_definitions(("15m", "1h", "4h", "24h", "7d"))]
        self.assertIn("exchange_inflow_usd_15m", names)
        self.assertIn("exchange_inflow_usd_24h", names)  # the label is the window as written


# --------------------------------------------------------------------------
# lagged-only normalisation
# --------------------------------------------------------------------------


def observations(values, *, start=0, step=HOUR):
    return [Observation(start + i * step, v) for i, v in enumerate(values)]


class NormalisationFunctionTests(unittest.TestCase):
    HISTORY = observations([float(v) for v in range(1, 11)])  # times 0..9h
    AS_OF = 10 * HOUR

    def test_only_strictly_earlier_and_recent_observations_are_used(self) -> None:
        obs = observations([1.0, None, 3.0, 4.0]) + [Observation(4 * HOUR, 99.0), Observation(5 * HOUR, 99.0)]
        self.assertEqual(trailing_values(obs, 4 * HOUR, 10 * HOUR), [1.0, 3.0, 4.0])  # not at or after as_of; not None
        self.assertEqual(trailing_values(obs, 4 * HOUR, 2 * HOUR), [3.0, 4.0])  # lookback is respected

    def test_zscore_matches_a_hand_calculation(self) -> None:
        z = rolling_zscore(self.HISTORY, self.AS_OF, 20.0, lookback_ms=20 * HOUR)
        expected = (20.0 - statistics.fmean(range(1, 11))) / statistics.stdev(range(1, 11))
        self.assertAlmostEqual(z, expected)

    def test_changing_the_future_cannot_change_a_normalised_value(self) -> None:
        with_future = self.HISTORY + [Observation(self.AS_OF, 1e9), Observation(self.AS_OF + HOUR, -1e9)]
        for fn in (rolling_zscore, rolling_percentile, ratio_to_trailing_median, ratio_to_trailing_mean):
            base = fn(self.HISTORY, self.AS_OF, 7.5, lookback_ms=20 * HOUR)
            self.assertEqual(fn(with_future, self.AS_OF, 7.5, lookback_ms=20 * HOUR), base, fn.__name__)

    def test_the_value_being_normalised_is_not_part_of_its_own_history(self) -> None:
        with_itself = self.HISTORY + [Observation(self.AS_OF, 20.0)]
        self.assertEqual(
            rolling_zscore(with_itself, self.AS_OF, 20.0, lookback_ms=20 * HOUR),
            rolling_zscore(self.HISTORY, self.AS_OF, 20.0, lookback_ms=20 * HOUR),
        )

    def test_too_little_history_or_no_variation_or_a_missing_current_is_none(self) -> None:
        short = observations([1.0, 2.0, 3.0])
        for fn in (rolling_zscore, rolling_percentile, ratio_to_trailing_median, ratio_to_trailing_mean):
            self.assertIsNone(fn(short, 3 * HOUR, 2.0, lookback_ms=10 * HOUR), fn.__name__)
            self.assertIsNone(fn(self.HISTORY, self.AS_OF, None, lookback_ms=20 * HOUR), fn.__name__)
        flat = observations([5.0] * 12)
        self.assertIsNone(rolling_zscore(flat, 12 * HOUR, 6.0, lookback_ms=20 * HOUR))  # undefined, not 0
        self.assertIsNone(ratio_to_trailing_median(observations([0.0] * 12), 12 * HOUR, 6.0, lookback_ms=20 * HOUR))

    def test_percentile_counts_ties_as_half(self) -> None:
        obs = observations([1, 2, 3, 4, 5, 5, 5, 6, 7, 8])
        self.assertEqual(rolling_percentile(obs, 10 * HOUR, 5.0, lookback_ms=20 * HOUR), 0.55)
        self.assertEqual(rolling_percentile(obs, 10 * HOUR, 100.0, lookback_ms=20 * HOUR), 1.0)

    def test_ratios_to_the_typical_level(self) -> None:
        self.assertEqual(ratio_to_trailing_median(self.HISTORY, self.AS_OF, 11.0, lookback_ms=20 * HOUR), 2.0)
        self.assertAlmostEqual(ratio_to_trailing_mean(self.HISTORY, self.AS_OF, 11.0, lookback_ms=20 * HOUR), 2.0)
        self.assertAlmostEqual(share_of_trailing_total(self.HISTORY, self.AS_OF, 11.0, lookback_ms=20 * HOUR), 2.0)

    def test_asset_relative_needs_a_positive_reference(self) -> None:
        self.assertEqual(asset_relative(50.0, 200.0), 0.25)
        for reference in (None, 0.0, -5.0):
            self.assertIsNone(asset_relative(50.0, reference))
        self.assertIsNone(asset_relative(None, 200.0))


class NormalisedFeatureBuilderTests(unittest.TestCase):
    DECISION = T0 + 30 * HOUR
    SPEC = NormalizedFeatureSpec("z", "exchange_inflow_usd_1h", Method.ZSCORE, 24 * HOUR, min_observations=5)

    def hourly(self, hours, *, amount=lambda k: 100_000 * (1 + k % 5), lag=MINUTE):
        return [
            make_event(k, event_time=T0 + k * HOUR - 30 * MINUTE, received=T0 + k * HOUR - 30 * MINUTE + lag,
                       usd=str(amount(k)))
            for k in hours
        ]

    def builder(self, events, coverage=None, spec=None):
        pl = FeaturePipeline(InMemoryEventSource(events), coverage or full_coverage(T0 - 100 * HOUR, T0 + 100 * HOUR))
        return NormalizedFeatureBuilder(pl, [spec or self.SPEC], step_ms=HOUR), pl

    def test_it_agrees_with_a_direct_calculation_from_point_in_time_values(self) -> None:
        events = self.hourly(range(0, 31))
        builder, pl = self.builder(events)
        got = builder.compute("BTC", self.DECISION)["z"]
        grid = [self.DECISION - k * HOUR for k in range(24, 0, -1)]
        series = build_series(pl, "BTC", "exchange_inflow_usd_1h", grid)
        now = pl.compute("BTC", self.DECISION, names=["exchange_inflow_usd_1h"]).get("exchange_inflow_usd_1h")
        expected = rolling_zscore(series, self.DECISION, now.as_float(), lookback_ms=24 * HOUR, min_observations=5)
        self.assertEqual(got.status, FeatureStatus.OK)
        self.assertAlmostEqual(got.value, expected)

    def test_events_after_the_decision_time_cannot_change_it(self) -> None:
        base, _ = self.builder(self.hourly(range(0, 31)))
        extended, _ = self.builder(self.hourly(range(0, 31)) + self.hourly(range(31, 40), amount=lambda k: 90_000_000))
        self.assertEqual(base.compute("BTC", self.DECISION)["z"].value,
                         extended.compute("BTC", self.DECISION)["z"].value)

    def test_history_is_what_was_known_then_not_what_we_know_now(self) -> None:
        # Hour 10's inflow is reported ten hours late. Recomputing the past with
        # today's knowledge would show it at hour 10; the point-in-time series does not.
        events = self.hourly([10], amount=lambda k: 7_000_000, lag=10 * HOUR)
        _, pl = self.builder(events)
        at_hour_10 = build_series(pl, "BTC", "exchange_inflow_usd_1h", [T0 + 10 * HOUR])[0]
        self.assertEqual(at_hour_10.value, 0.0)
        at_hour_20 = build_series(pl, "BTC", "exchange_inflow_usd_1h", [T0 + 10 * HOUR + 9 * MINUTE + 10 * HOUR])[0]
        self.assertEqual(at_hour_20.value, 0.0)  # by then the event is outside the one-hour window

    def test_too_little_history_is_stated_and_not_guessed(self) -> None:
        events = self.hourly(range(0, 4))
        builder, _ = self.builder(events, coverage=full_coverage(T0, T0 + 100 * HOUR))
        got = builder.compute("BTC", T0 + 3 * HOUR + 40 * MINUTE)["z"]
        self.assertEqual((got.value, got.status), (None, FeatureStatus.MISSING_NO_HISTORY))

    def test_a_missing_base_feature_makes_the_normalised_one_missing(self) -> None:
        builder, _ = self.builder(self.hourly(range(0, 31)), coverage=CoverageIndex())
        got = builder.compute("BTC", self.DECISION)["z"]
        self.assertEqual((got.value, got.status), (None, FeatureStatus.MISSING_NO_COVERAGE))
        self.assertIn("base feature", got.detail)

    def test_the_default_specs_are_the_three_named_in_the_brief(self) -> None:
        self.assertEqual(
            [s.name for s in default_normalized_specs()],
            ["exchange_inflow_zscore_30d", "whale_trade_flow_percentile_30d", "transfer_value_vs_30d_median"],
        )
        with self.assertRaises(ValueError):
            NormalizedFeatureBuilder(pipeline([]), step_ms=0)


if __name__ == "__main__":
    unittest.main()
