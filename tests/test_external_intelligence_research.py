"""Price lookups, latency, event studies, splits, the ridge model, statistics, evidence, hypotheses."""

from __future__ import annotations

import random
import statistics
import tempfile
import unittest
from decimal import Decimal

from crypto_research.experiments.registry import ExperimentRegistry

from external_intelligence.attribution import EntityType
from external_intelligence.events import Side
from external_intelligence.features.definitions import default_feature_definitions
from external_intelligence.features.normalization import default_normalized_specs
from external_intelligence.research.baseline import BASELINE_FEATURES, BaselineFeatureBuilder
from external_intelligence.features.normalization import Observation
from external_intelligence.research.event_study import (
    DISCLAIMER,
    EventFilter,
    EventStudySpec,
    run_event_study,
    run_trigger_study,
)
from external_intelligence.research.triggers import exceedance_triggers
from external_intelligence.research.evidence import (
    EvidenceAssessment,
    EvidenceLevel,
    classify_event,
    classify_flow_cluster,
    observed_response,
)
from external_intelligence.research.hypotheses import HYPOTHESES, all_hypotheses, count_prior_runs
from external_intelligence.research.latency import analyze_latency
from external_intelligence.research.linear import PrevailingMean, RidgeModel, mse
from external_intelligence.research.price import BarPriceSource, forward_return
from external_intelligence.research.splits import (
    Fold,
    HoldoutSpec,
    WalkForwardSpec,
    assert_no_leakage,
    holdout_folds,
    walk_forward_folds,
)
from external_intelligence.research.stats import (
    block_bootstrap_mean,
    effective_samples,
    holm_adjust,
    percentile,
    summary_stats,
)
from external_intelligence.events import EventType

from .ei_helpers import HOUR, MINUTE, T0, make_bars, make_event


def linear_bars(count=200, start_price=100):
    """close_i = start_price + i, one bar per hour; bar i is observable at T0 + (i+1)h - 1."""
    return make_bars([str(start_price + i) for i in range(count)])


def observable(i: int, feed_delay: int = 0) -> int:
    return T0 + (i + 1) * HOUR - 1 + feed_delay


# --------------------------------------------------------------------------
# price at a time
# --------------------------------------------------------------------------


class PriceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BarPriceSource(linear_bars(48))

    def test_the_price_at_a_time_is_the_last_completed_bar_never_the_forming_one(self) -> None:
        self.assertEqual(self.source.price_at(observable(19)).price, Decimal(119))
        self.assertEqual(self.source.price_at(observable(19) - 1).price, Decimal(118))
        # Half way through bar 20: still bar 19's close, not a look at bar 20.
        self.assertEqual(self.source.price_at(T0 + 20 * HOUR + 30 * MINUTE).price, Decimal(119))

    def test_no_price_before_the_first_bar_completes_and_none_that_is_too_old(self) -> None:
        self.assertIsNone(self.source.price_at(observable(0) - 1))
        self.assertIsNotNone(self.source.price_at(self.source.last_observable + 2 * HOUR))
        self.assertIsNone(self.source.price_at(self.source.last_observable + 2 * HOUR + 1))

    def test_a_slower_feed_makes_bars_observable_later(self) -> None:
        slow = BarPriceSource(linear_bars(48), extra_delay_ms=30 * MINUTE)
        self.assertEqual(slow.price_at(observable(19)).price, Decimal(118))
        self.assertEqual(slow.price_at(observable(19) + 30 * MINUTE).price, Decimal(119))

    def test_bars_that_become_observable_out_of_order_are_refused(self) -> None:
        bars = make_bars(["100", "101", "102"])
        late_first = make_bars(["100"], feed_delay_ms=10 * HOUR) + bars[1:]
        with self.assertRaises(ValueError):
            BarPriceSource(late_first)
        with self.assertRaises(ValueError):
            BarPriceSource([])

    def test_forward_return_uses_the_price_known_at_entry_and_at_the_horizon(self) -> None:
        entry = T0 + 21 * HOUR + 10 * MINUTE  # bar 20 (close 120) is the latest known
        value, reason = forward_return(self.source, entry, HOUR)
        self.assertIsNone(reason)
        self.assertAlmostEqual(value, 121 / 120 - 1)
        value, _ = forward_return(self.source, entry, 4 * HOUR)
        self.assertAlmostEqual(value, 124 / 120 - 1)

    def test_a_horizon_finer_than_the_bars_or_beyond_the_data_has_no_return(self) -> None:
        entry = T0 + 21 * HOUR + 10 * MINUTE
        self.assertEqual(forward_return(self.source, entry, 5 * MINUTE), (None, "horizon_finer_than_resolution"))
        self.assertEqual(forward_return(self.source, entry, 100 * HOUR), (None, "horizon_beyond_data"))
        self.assertEqual(forward_return(self.source, T0, HOUR), (None, "no_price_at_entry"))


# --------------------------------------------------------------------------
# latency
# --------------------------------------------------------------------------


class LatencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BarPriceSource(linear_bars(48))
        event_time = T0 + 20 * HOUR + 30 * MINUTE
        self.event = make_event(
            1, event_time=event_time, provider_publish_time=event_time + 10 * MINUTE,
            received=event_time + 40 * MINUTE,
        )

    def test_price_at_receipt_is_the_last_bar_known_when_we_received_it(self) -> None:
        record = analyze_latency([self.event], self.source).records[0]
        self.assertEqual(record.price_at_event_time, "119")
        self.assertEqual(record.price_at_provider_publish_time, "119")
        self.assertEqual(record.price_at_system_received_time, "120")  # bar 20 completed before receipt

    def test_returns_between_the_three_moments_and_after_receipt(self) -> None:
        record = analyze_latency([self.event], self.source).records[0]
        self.assertIsNone(record.return_event_to_publish)  # both inside one bar: unresolvable, not zero
        self.assertAlmostEqual(record.return_event_to_receive, 120 / 119 - 1)
        after = record.returns_after_receipt
        self.assertIsNone(after["return_receive_to_5m"])
        self.assertAlmostEqual(after["return_receive_to_1h"], 121 / 120 - 1)
        self.assertAlmostEqual(after["return_receive_to_4h"], 124 / 120 - 1)
        self.assertAlmostEqual(after["return_receive_to_24h"], 144 / 120 - 1)

    def test_an_event_with_no_receipt_time_is_skipped_and_estimates_need_permission(self) -> None:
        unknown = make_event(2, event_time=T0 + 10 * HOUR)
        estimated = make_event(3, event_time=T0 + 10 * HOUR, estimated=(T0 + 10 * HOUR + 5 * MINUTE, 5 * MINUTE, "why"))
        report = analyze_latency([unknown, estimated], self.source)
        self.assertEqual(report.summary["skipped"], {"no_receipt_time": 2})
        allowed = analyze_latency([unknown, estimated], self.source, allow_estimated=True)
        self.assertEqual((allowed.summary["usable"], allowed.summary["skipped"]), (1, {"no_receipt_time": 1}))

    def test_the_summary_states_the_resolution_and_what_was_measurable(self) -> None:
        summary = analyze_latency([self.event], self.source).summary
        self.assertEqual(summary["price_resolution_ms"], HOUR)
        self.assertEqual(summary["metrics"]["return_receive_to_5m"]["n"], 0)
        self.assertEqual(summary["metrics"]["return_receive_to_1h"]["n"], 1)


# --------------------------------------------------------------------------
# event studies
# --------------------------------------------------------------------------


def hourly_events(count=100, start_hour=30, **kwargs):
    return [
        make_event(k, event_time=T0 + (start_hour + k) * HOUR, received=T0 + (start_hour + k) * HOUR + 5 * MINUTE, **kwargs)
        for k in range(count)
    ]


INFLOW = EventFilter((EventType.EXCHANGE_INFLOW,))


class EventStudyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = BarPriceSource(linear_bars(200))
        self.events = hourly_events()

    def horizon(self, result, label):
        return next(h for h in result.horizons if h["horizon"] == label)

    def test_entry_is_the_receipt_time_and_returns_match_a_hand_calculation(self) -> None:
        result = run_event_study(self.events, self.source, EventStudySpec("s", INFLOW, ("24h",), min_independent=5))
        h = self.horizon(result, "24h")
        picked = [0, 24, 48, 72, 96]  # greedy non-overlapping entries, one horizon apart
        expected = [(153 + k) / (129 + k) - 1 for k in picked]
        self.assertEqual((h["n_raw"], h["n_independent"]), (100, 5))
        self.assertAlmostEqual(h["after_event_independent"]["median"], statistics.median(expected))
        self.assertAlmostEqual(h["after_event_independent"]["mean"], statistics.fmean(expected))
        self.assertEqual(h["after_event_independent"]["positive_frequency"], 1.0)
        self.assertTrue(h["interpretable"])

    def test_the_unconditional_baseline_and_pre_event_move_sit_alongside(self) -> None:
        h = self.horizon(run_event_study(self.events, self.source, EventStudySpec("s", INFLOW, ("24h",))), "24h")
        self.assertEqual(h["unconditional_baseline"]["n"], 176)  # every bar whose 24h outcome is in the data
        self.assertGreater(h["pre_event_move"]["median"], 0)
        self.assertEqual(h["mean_max_drawdown_after"], 0.0)  # a steadily rising price never dips
        self.assertFalse(h["interpretable"])  # 5 independent events < the default minimum of 30

    def test_horizons_finer_than_the_bars_are_reported_as_not_computable(self) -> None:
        result = run_event_study(self.events, self.source, EventStudySpec("s", INFLOW, ("5m", "1h")))
        five = self.horizon(result, "5m")
        self.assertEqual(five["n_raw"], 0)
        self.assertEqual(five["not_computable"], {"horizon_finer_than_resolution": 100})
        self.assertEqual(self.horizon(result, "1h")["n_raw"], 100)

    def test_post_event_drawdown_is_measured_from_the_entry_price(self) -> None:
        closes = ["100"] * 60 + ["90"] * 3 + ["100"] * 40
        source = BarPriceSource(make_bars(closes))
        event = make_event(1, event_time=T0 + 50 * HOUR, received=T0 + 50 * HOUR + MINUTE)
        h = self.horizon(run_event_study([event], source, EventStudySpec("s", INFLOW, ("24h",), min_independent=1)), "24h")
        self.assertAlmostEqual(h["mean_max_drawdown_after"], -0.1)

    def test_filters_by_type_asset_provider_and_minimum_amount_and_counts_unknown_amounts(self) -> None:
        events = hourly_events(10) + [
            make_event(100, event_type="EXCHANGE_OUTFLOW", event_time=T0 + 50 * HOUR, received=T0 + 50 * HOUR),
            make_event(101, asset="ETH", event_time=T0 + 50 * HOUR, received=T0 + 50 * HOUR),
            make_event(102, usd=None, event_time=T0 + 50 * HOUR, received=T0 + 50 * HOUR),
            make_event(103, usd="9000000", event_time=T0 + 50 * HOUR, received=T0 + 50 * HOUR),
        ]
        spec = EventStudySpec("s", EventFilter((EventType.EXCHANGE_INFLOW,), assets=("BTC",), providers=("whale_alert",),
                                               min_amount_usd=Decimal("5000000")), ("1h",))
        result = run_event_study(events, self.source, spec)
        self.assertEqual(result.events_matching, 1)
        self.assertEqual(result.skipped["unknown_amount_excluded"], 1)

    def test_events_without_a_receipt_time_are_skipped_not_guessed(self) -> None:
        events = [make_event(1, event_time=T0 + 50 * HOUR)]
        result = run_event_study(events, self.source, EventStudySpec("s", INFLOW, ("1h",)))
        self.assertEqual((result.events_usable, result.skipped), (0, {"no_receipt_time": 1}))

    def test_regimes_look_only_backwards_and_say_unknown_when_history_is_short(self) -> None:
        source = BarPriceSource(linear_bars(1000))
        events = [make_event(1, event_time=T0 + 100 * HOUR, received=T0 + 100 * HOUR + MINUTE),
                  make_event(2, event_time=T0 + 800 * HOUR, received=T0 + 800 * HOUR + MINUTE)]
        trend = run_event_study(events, source, EventStudySpec("s", INFLOW, ("1h",), regime="trend"))
        self.assertEqual(set(self.horizon(trend, "1h")["regimes"]), {"UNKNOWN", "UPTREND"})
        vol = run_event_study(events, source, EventStudySpec("s", INFLOW, ("1h",), regime="volatility"))
        self.assertTrue(set(self.horizon(vol, "1h")["regimes"]) <= {"UNKNOWN", "RISING_VOL", "FALLING_VOL"})
        with self.assertRaises(ValueError):
            run_event_study(events, source, EventStudySpec("s", INFLOW, ("1h",), regime="astrology"))

    def test_the_causal_disclaimer_travels_with_every_result(self) -> None:
        result = run_event_study(self.events, self.source, EventStudySpec("s", INFLOW, ("1h",)))
        self.assertEqual(result.to_dict()["disclaimer"], DISCLAIMER)
        self.assertIn("do not show that the event caused", DISCLAIMER)


# --------------------------------------------------------------------------
# derived categories: an extreme reading of a feature
# --------------------------------------------------------------------------


def hourly_series(values, *, start=0):
    return [Observation(start + i * HOUR, v) for i, v in enumerate(values)]


class TriggerTests(unittest.TestCase):
    def test_a_reading_beyond_the_quantile_of_its_own_past_is_a_trigger(self) -> None:
        values = [float(i % 10) for i in range(60)]
        values[45] = 500.0
        found = exceedance_triggers("imbalance", hourly_series(values), lookback_ms=40 * HOUR, quantile=0.95)
        self.assertEqual([t.entry_time for t in found if t.value == 500.0], [45 * HOUR])
        spike = next(t for t in found if t.value == 500.0)
        self.assertEqual(spike.label, "imbalance")
        self.assertLess(spike.threshold, 10)

    def test_the_threshold_uses_only_strictly_earlier_history(self) -> None:
        values = [float(i % 10) for i in range(60)]
        values[45] = 500.0
        base = exceedance_triggers("x", hourly_series(values), lookback_ms=40 * HOUR, quantile=0.95)
        extended = exceedance_triggers("x", hourly_series(values + [1e9] * 20), lookback_ms=40 * HOUR, quantile=0.95)
        before_45 = [t for t in extended if t.entry_time <= 45 * HOUR]
        self.assertEqual([t for t in base if t.entry_time <= 45 * HOUR], before_45)  # the future cannot move a threshold

    def test_too_little_history_means_no_trigger_and_missing_values_are_skipped(self) -> None:
        series = hourly_series([1.0, 2.0, 999.0])
        self.assertEqual(exceedance_triggers("x", series, lookback_ms=10 * HOUR, quantile=0.9, min_observations=5), [])
        with_gaps = hourly_series([float(i % 5) for i in range(40)])
        with_gaps[30] = Observation(30 * HOUR, None)
        found = exceedance_triggers("x", with_gaps, lookback_ms=35 * HOUR, quantile=0.9)
        self.assertNotIn(30 * HOUR, [t.entry_time for t in found])

    def test_a_low_tail_and_bad_arguments(self) -> None:
        values = [float(i % 10) + 100 for i in range(60)]
        values[50] = -500.0
        low = exceedance_triggers("x", hourly_series(values), lookback_ms=40 * HOUR, quantile=0.05, direction="below")
        self.assertIn(50 * HOUR, [t.entry_time for t in low])
        for kwargs in ({"quantile": 0.0}, {"quantile": 1.0}, {"direction": "sideways", "quantile": 0.5},
                       {"quantile": 0.5, "min_observations": 1}):
            with self.assertRaises(ValueError):
                exceedance_triggers("x", hourly_series(values), lookback_ms=HOUR, **kwargs)

    def test_a_trigger_study_measures_forward_returns_from_the_moment_it_was_actionable(self) -> None:
        source = BarPriceSource(linear_bars(200))
        from external_intelligence.research.triggers import Trigger

        triggers = [Trigger(f"t{k}", T0 + (50 + 30 * k) * HOUR + 5 * MINUTE, "spike", 9.0, 1.0) for k in range(4)]
        result = run_trigger_study(triggers, source, EventStudySpec("study", horizons=("24h",), min_independent=3))
        h = result.horizons[0]
        starts = [100 + 49 + 30 * k for k in range(4)]  # the bar completed just before each entry
        expected = [(start + 24) / start - 1 for start in starts]
        self.assertEqual((result.events_matching, h["n_raw"], h["n_independent"]), (4, 4, 4))
        self.assertAlmostEqual(h["after_event_independent"]["mean"], statistics.fmean(expected))
        self.assertTrue(h["interpretable"])
        self.assertEqual(result.to_dict()["disclaimer"], DISCLAIMER)

    def test_an_event_study_needs_a_filter_and_a_trigger_study_does_not(self) -> None:
        source = BarPriceSource(linear_bars(50))
        with self.assertRaises(ValueError):
            run_event_study([], source, EventStudySpec("no filter"))
        self.assertEqual(run_trigger_study([], source, EventStudySpec("none", horizons=("1h",))).events_usable, 0)


# --------------------------------------------------------------------------
# chronological splits
# --------------------------------------------------------------------------


class SplitTests(unittest.TestCase):
    TIMES = [i * HOUR for i in range(100)]
    HORIZON = 5 * HOUR

    def test_holdout_trains_on_the_past_and_purges_samples_whose_outcome_reaches_the_test(self) -> None:
        folds = {f.kind: f for f in holdout_folds(self.TIMES, self.HORIZON, HoldoutSpec(50 * HOUR, 75 * HOUR))}
        validation, test = folds["validation"], folds["test"]
        self.assertEqual(validation.train, tuple(range(0, 46)))  # 46..49 would see into the validation period
        self.assertEqual(validation.test, tuple(range(50, 75)))
        self.assertEqual(test.train, tuple(range(0, 71)))
        self.assertEqual(test.test, tuple(range(75, 100)))
        for fold in (validation, test):
            assert_no_leakage(self.TIMES, self.HORIZON, fold)
            self.assertLess(max(fold.train), min(fold.test))

    def test_walk_forward_expands_the_training_window_over_consecutive_test_blocks(self) -> None:
        folds = walk_forward_folds(self.TIMES, self.HORIZON, WalkForwardSpec(40 * HOUR, 20 * HOUR))
        self.assertEqual([f.name for f in folds], ["wf_01", "wf_02", "wf_03"])
        self.assertEqual([len(f.train) for f in folds], [36, 56, 76])
        self.assertEqual([f.test[0] for f in folds], [40, 60, 80])
        seen: set[int] = set()
        for fold in folds:
            assert_no_leakage(self.TIMES, self.HORIZON, fold)
            self.assertFalse(seen & set(fold.test))
            seen |= set(fold.test)

    def test_a_leaking_fold_is_caught(self) -> None:
        with self.assertRaises(AssertionError):
            assert_no_leakage(self.TIMES, self.HORIZON, Fold("bad", "test", (10, 48), (50, 51)))

    def test_samples_are_never_reordered(self) -> None:
        with self.assertRaises(ValueError):
            holdout_folds([3 * HOUR, HOUR, 2 * HOUR], HOUR, HoldoutSpec(HOUR, 2 * HOUR))
        with self.assertRaises(ValueError):
            walk_forward_folds([3 * HOUR, HOUR], HOUR, WalkForwardSpec(HOUR, HOUR))
        with self.assertRaises(ValueError):
            holdout_folds(self.TIMES, HOUR, HoldoutSpec(50 * HOUR, 50 * HOUR))


# --------------------------------------------------------------------------
# baseline features, the model, statistics
# --------------------------------------------------------------------------


class BaselineFeatureTests(unittest.TestCase):
    def test_features_use_only_bars_observable_by_the_decision_time(self) -> None:
        bars = make_bars([str(100 + (i % 7)) for i in range(150)])
        builder = BaselineFeatureBuilder(bars)
        at = builder.features_at(observable(120))
        changed = make_bars([str(100 + (i % 7)) for i in range(121)] + ["999"] * 29)
        self.assertEqual(BaselineFeatureBuilder(changed).features_at(observable(120)), at)
        self.assertTrue(all(at[name] is not None for name in BASELINE_FEATURES))

    def test_too_little_history_is_none_not_zero(self) -> None:
        builder = BaselineFeatureBuilder(make_bars([str(100 + i) for i in range(30)]))
        got = builder.features_at(observable(29))
        self.assertIsNone(got["trend_24_96"])
        self.assertIsNotNone(got["ret_24"])
        self.assertTrue(all(v is None for v in builder.features_at(observable(0)).values()))

    def test_a_gap_in_the_bars_makes_spanning_features_missing(self) -> None:
        bars = make_bars([str(100 + i) for i in range(150)])
        gapped = bars[:60] + bars[62:]  # two bars missing
        got = BaselineFeatureBuilder(gapped).features_at(gapped[80].observable_at)
        self.assertIsNone(got["ret_24"])  # the 24 bars back would really be 26 hours back
        self.assertIsNone(got["vol_24"])
        self.assertIsNotNone(got["ret_1"])
        later = BaselineFeatureBuilder(gapped).features_at(gapped[-1].observable_at)
        self.assertIsNotNone(later["ret_24"])  # far enough past the gap


class RidgeTests(unittest.TestCase):
    ROWS = [[float(a), float(b)] for a in range(-3, 4) for b in range(-3, 4)]
    Y = [2 * a - 3 * b + 5 for a, b in ROWS]

    def test_it_recovers_a_linear_relationship(self) -> None:
        model = RidgeModel(1e-6).fit(self.ROWS, self.Y)
        for got, want in zip(model.predict([[10.0, -10.0], [0.0, 0.0]]), [55.0, 5.0]):
            self.assertAlmostEqual(got, want, places=3)

    def test_a_larger_penalty_shrinks_predictions_towards_the_mean(self) -> None:
        loose = RidgeModel(1e-6).fit(self.ROWS, self.Y).predict(self.ROWS)
        tight = RidgeModel(1e6).fit(self.ROWS, self.Y).predict(self.ROWS)
        self.assertLess(statistics.pstdev(tight), statistics.pstdev(loose) * 0.01)

    def test_prediction_uses_only_training_statistics(self) -> None:
        model = RidgeModel(1.0).fit(self.ROWS, self.Y)
        alone = model.predict([[1.0, 1.0]])
        crowd = model.predict([[1.0, 1.0], [1e9, -1e9], [3.0, 3.0]])
        self.assertEqual(alone[0], crowd[0])  # other test rows cannot move a prediction

    def test_a_constant_column_is_harmless_and_bad_arguments_are_refused(self) -> None:
        rows = [[a, b, 7.0] for a, b in self.ROWS]
        predictions = RidgeModel(1.0).fit(rows, self.Y).predict(rows)
        self.assertEqual(len(predictions), len(rows))
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                RidgeModel(bad)
        with self.assertRaises(ValueError):
            RidgeModel(1.0).fit([], [])

    def test_fitting_is_deterministic_and_the_benchmark_is_the_training_mean(self) -> None:
        first = RidgeModel(2.0).fit(self.ROWS, self.Y).predict(self.ROWS)
        second = RidgeModel(2.0).fit(self.ROWS, self.Y).predict(self.ROWS)
        self.assertEqual(first, second)
        self.assertEqual(PrevailingMean().fit([1.0, 2.0, 6.0]).predict(3), [3.0, 3.0, 3.0])
        self.assertEqual(mse([1.0, 2.0], [1.0, 4.0]), 2.0)


class StatsTests(unittest.TestCase):
    def test_summary_stats_and_empty_input(self) -> None:
        s = summary_stats([1.0, 2.0, 3.0, 4.0, -1.0])
        self.assertEqual((s["n"], s["median"], s["mean"], s["positive_frequency"]), (5, 2.0, 1.8, 0.8))
        self.assertEqual(summary_stats([])["n"], 0)
        self.assertIsNone(summary_stats([])["median"])
        self.assertIsNone(summary_stats([5.0])["stdev"])

    def test_overlap_shrinks_the_effective_sample(self) -> None:
        self.assertEqual(effective_samples(240, 24), 10.0)
        self.assertEqual(effective_samples(240, 0.5), 240.0)  # never inflated

    def test_percentile_interpolates(self) -> None:
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.5), 2.5)
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 1.0), 4.0)

    def test_holm_adjustment_matches_the_textbook_example(self) -> None:
        self.assertEqual(holm_adjust([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])
        self.assertEqual(holm_adjust([0.5]), [0.5])
        self.assertEqual(holm_adjust([0.9, 0.9]), [1.0, 1.0])

    def test_block_bootstrap_detects_a_real_mean_and_not_its_opposite(self) -> None:
        rng = random.Random(3)
        positive = [rng.gauss(0.3, 1.0) for _ in range(400)]
        up = block_bootstrap_mean(positive, block_length=5, iterations=400, seed=1)
        down = block_bootstrap_mean([-v for v in positive], block_length=5, iterations=400, seed=1)
        self.assertLess(up["p_one_sided"], 0.02)
        self.assertGreater(down["p_one_sided"], 0.9)
        self.assertLess(up["ci_low"], up["mean"])
        self.assertLess(up["mean"], up["ci_high"])
        self.assertGreater(up["ci_low"], 0)

    def test_block_bootstrap_is_seeded_and_refuses_to_run_on_too_little_data(self) -> None:
        values = [random.Random(9).gauss(0, 1) for _ in range(200)]
        a = block_bootstrap_mean(values, block_length=4, iterations=200, seed=5)
        b = block_bootstrap_mean(values, block_length=4, iterations=200, seed=5)
        self.assertEqual(a, b)
        short = block_bootstrap_mean(values[:10], block_length=5, iterations=200, seed=5)
        self.assertTrue(short["insufficient"])
        self.assertIsNone(short["p_one_sided"])


# --------------------------------------------------------------------------
# evidence ladder and hypotheses
# --------------------------------------------------------------------------


class EvidenceTests(unittest.TestCase):
    def test_a_transfer_is_level_1_until_the_provider_names_someone(self) -> None:
        raw = make_event(1)
        named = make_event(2, source_entity="some fund")
        typed = make_event(3, destination_entity_type=EntityType.EXCHANGE)
        unknown = make_event(4, source_entity_type=EntityType.UNKNOWN)
        self.assertIs(classify_event(raw).level, EvidenceLevel.RAW_TRANSFER)
        self.assertIs(classify_event(named).level, EvidenceLevel.ATTRIBUTED_TRANSFER)
        self.assertIs(classify_event(typed).level, EvidenceLevel.ATTRIBUTED_TRANSFER)
        self.assertIs(classify_event(unknown).level, EvidenceLevel.RAW_TRANSFER)

    def test_executed_activity_is_level_4_and_derivatives_readings_are_off_the_ladder(self) -> None:
        trade = make_event(1, provider="coinlobster", event_type="WHALE_TRADE_BUY", side=Side.BUY)
        liquidation = make_event(2, provider="coinlobster", event_type="LONG_LIQUIDATION")
        funding = make_event(3, provider="coinlobster", event_type="FUNDING_UPDATE", usd=None, value=Decimal("0.0001"), value_unit="x")
        self.assertIs(classify_event(trade).level, EvidenceLevel.EXECUTED_MARKET_ACTIVITY)
        self.assertIs(classify_event(liquidation).level, EvidenceLevel.EXECUTED_MARKET_ACTIVITY)
        self.assertIsNone(classify_event(funding))

    def test_a_cluster_needs_several_similar_transfers_in_one_direction(self) -> None:
        inflows = [make_event(i) for i in range(3)]
        self.assertIs(classify_flow_cluster(inflows).level, EvidenceLevel.AGGREGATE_REPEATED_FLOW)
        self.assertIsNone(classify_flow_cluster(inflows[:2]))
        self.assertIsNone(classify_flow_cluster(inflows[:2] + [make_event(9, event_type="EXCHANGE_OUTFLOW")]))
        self.assertIsNone(classify_flow_cluster(inflows[:2] + [make_event(9, asset="ETH")]))

    def test_a_measured_response_needs_enough_independent_events_and_is_only_a_measurement(self) -> None:
        self.assertIsNone(observed_response(29))
        self.assertIs(observed_response(30).level, EvidenceLevel.OBSERVED_MARKET_RESPONSE)

    def test_there_is_no_score_and_no_confidence_percentage_anywhere(self) -> None:
        self.assertEqual(set(EvidenceAssessment.__dataclass_fields__), {"level", "rationale"})
        self.assertEqual([lvl.value for lvl in EvidenceLevel], [1, 2, 3, 4, 5])
        for level in EvidenceLevel:
            self.assertNotIn("%", level.name)


class HypothesisTests(unittest.TestCase):
    def test_the_five_hypotheses_are_registered_untested_with_a_mechanism_and_controls(self) -> None:
        self.assertEqual([h.id for h in all_hypotheses()], ["A", "B", "C", "D", "E"])
        for h in all_hypotheses():
            self.assertEqual(h.status, "UNTESTED", h.id)
            self.assertTrue(h.mechanism.strip() and h.caveat.strip(), h.id)
            self.assertEqual(h.required_controls, BASELINE_FEATURES)
            self.assertTrue(set(h.experiments) <= {1, 2, 3})

    def test_every_feature_a_hypothesis_names_exists(self) -> None:
        known = {d.name for d in default_feature_definitions()} | {s.name for s in default_normalized_specs()}
        for h in all_hypotheses():
            self.assertTrue(set(h.feature_names) <= known, (h.id, set(h.feature_names) - known))

    def test_the_wording_follows_the_brief(self) -> None:
        self.assertIn("weaker forward returns", HYPOTHESES["A"].statement)
        self.assertIn("weak or regime-dependent", HYPOTHESES["B"].statement)
        self.assertIn("more directional information", HYPOTHESES["C"].statement)
        self.assertIn("short-horizon", HYPOTHESES["D"].statement)
        self.assertIn("direction cannot be inferred", HYPOTHESES["E"].statement)

    def test_prior_runs_are_counted_from_the_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(count_prior_runs(ExperimentRegistry(tmp), experiment_name="anything"), 0)


if __name__ == "__main__":
    unittest.main()
