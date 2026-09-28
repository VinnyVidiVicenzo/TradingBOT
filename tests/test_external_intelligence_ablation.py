"""The ablation harness: one common sample, chronological folds, reproducible, honest about failure."""

from __future__ import annotations

import dataclasses
import json
import math
import random
import tempfile
import unittest
from pathlib import Path

from crypto_research.experiments.registry import ExperimentRegistry

from external_intelligence.base.coverage import CoverageIndex, CoverageInterval
from external_intelligence.base.health import HealthStatus
from external_intelligence.features.definitions import (
    executed_definitions,
    liquidation_definitions,
    transfer_definitions,
)
from external_intelligence.research.ablation import (
    ABLATION_VERSION,
    AblationInputs,
    AblationSpec,
    dataset_fingerprint,
    run_ablation,
)
from external_intelligence.research.hypotheses import count_prior_runs, runs_for_hypothesis
from external_intelligence.research.registry_bridge import (
    STRATEGY_NAME,
    preregistration_text,
    run_and_record,
)
from external_intelligence.research.splits import HoldoutSpec, WalkForwardSpec

from .ei_helpers import HOUR, MINUTE, T0, full_coverage, make_bars, make_event

N = 1000
TRANSFERS = tuple(transfer_definitions(("1h",)))
TRANSFERS_AND_TRADES = tuple(transfer_definitions(("1h",)) + executed_definitions(("1h",)))


class Market:
    """Synthetic hourly bars and one exchange-inflow event per hour.

    ``x`` is a persistent hidden driver. It moves *later* prices (twelve hours on),
    so what an event reports about it is not yet visible in earlier prices.
    """

    def __init__(self, signal: float, *, late_hours: int = 0, seed: int = 11, n: int = N) -> None:
        rng = random.Random(seed)
        x, previous = [], 0.0
        for _ in range(n):
            previous = 0.95 * previous + math.sqrt(1 - 0.95 ** 2) * rng.gauss(0, 1)
            x.append(previous)
        closes, price = [], 100.0
        for i in range(n):
            driver = x[i - 12] if i >= 12 else 0.0
            price *= math.exp(signal * driver + rng.gauss(0, 0.005))
            closes.append(round(price, 6))
        self.x, self.closes, self.n = x, closes, n
        self.bars = make_bars(closes)
        self.events = [self.event(k, 1_000_000 * (1 + 0.3 * x[k]), 60_000 + late_hours * HOUR) for k in range(n)]

    @staticmethod
    def event(k: int, amount: float, lag_ms: int):
        decision = T0 + k * HOUR
        return make_event(k, event_time=decision - 30 * MINUTE, received=decision - 30 * MINUTE + lag_ms,
                          usd=f"{max(1.0, amount):.2f}")

    def with_events(self, events) -> "Market":
        clone = object.__new__(Market)
        clone.__dict__.update(self.__dict__)
        clone.events = list(events)
        return clone

    def cheating_events(self, lag_ms: int):
        """Amounts that reveal each decision's *future* return, reported with the given lag."""
        out = []
        for k in range(24, self.n - 24):
            future = self.closes[k + 23] / self.closes[k - 1] - 1
            out.append(self.event(k, 1_000_000 * (1 + 5 * future), lag_ms))
        return out

    def inputs(self, *, definitions=TRANSFERS, coverage=None, events=None) -> AblationInputs:
        return AblationInputs(
            self.events if events is None else events,
            coverage if coverage is not None else full_coverage(T0 - 200 * HOUR, T0 + (self.n + 100) * HOUR),
            self.bars, definitions=definitions, provider_versions={"whale_alert": "test-fixture"},
        )


def spec(**overrides) -> AblationSpec:
    fields = dict(
        name="probe", asset="BTC", market_symbol="BTCUSDT", interval="1h",
        start=T0 + 120 * HOUR, end=T0 + (N - 30) * HOUR, step_ms=HOUR, horizon_ms=24 * HOUR,
        mode="walk_forward", walk_forward=WalkForwardSpec(300 * HOUR, 150 * HOUR),
        experiments=(0, 1), latency_stress_ms=(15 * MINUTE, 45 * MINUTE), bootstrap_iterations=300,
        min_common_samples=100, min_effective_samples=10, hypotheses=("A",),
    )
    fields.update(overrides)
    return AblationSpec(**fields)


def gain(result, k="1"):
    return result["experiments"][k]["vs_experiment_0"]


class DetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.signal = Market(0.002)
        cls.signal_result = run_ablation(spec(), cls.signal.inputs())

    def test_an_effect_that_market_data_cannot_see_is_detected(self) -> None:
        self.assertEqual(self.signal_result["status"], "COMPLETED")
        g = gain(self.signal_result)
        self.assertGreater(g["mse_improvement_pct"], 5)
        self.assertLess(g["p_one_sided"], 0.05)
        self.assertLess(g["p_holm"], 0.05)
        self.assertGreater(g["ci95_low"], 0)

    def test_no_effect_shows_no_improvement_and_no_criterion_set_is_passed(self) -> None:
        result = run_ablation(spec(), Market(0.0).inputs())
        self.assertLess(gain(result)["mse_improvement_pct"], 2)
        self.assertFalse(result["evidence_checklist"]["1"]["all_criteria_passed"])

    def test_events_that_arrive_after_the_decision_time_add_nothing(self) -> None:
        result = run_ablation(spec(), Market(0.002, late_hours=2).inputs())
        self.assertAlmostEqual(gain(result)["mse_improvement"], 0.0, places=12)

    def test_a_feed_that_reveals_the_future_is_useless_while_its_receipts_are_honest(self) -> None:
        market = Market(0.002)
        honest = run_ablation(spec(latency_stress_ms=()), market.inputs(events=market.cheating_events(25 * HOUR)))
        self.assertAlmostEqual(gain(honest)["mse_improvement"], 0.0, places=12)

    def test_the_same_feed_with_falsified_early_receipts_would_be_exploited_so_the_guard_is_what_stops_it(self) -> None:
        market = Market(0.002)
        falsified = run_ablation(spec(latency_stress_ms=()), market.inputs(events=market.cheating_events(60_000)))
        self.assertGreater(gain(falsified)["mse_improvement_pct"], 50)

    def test_the_baseline_never_sees_external_data(self) -> None:
        without = run_ablation(spec(), self.signal.inputs(events=[]))
        self.assertEqual(without["experiments"]["0"], self.signal_result["experiments"]["0"])

    def test_a_slower_feed_is_tested_and_the_gain_can_vanish(self) -> None:
        stress = self.signal_result["latency_stress"]
        self.assertTrue(stress[str(15 * MINUTE)]["experiments"]["1"]["improvement_persists"])
        self.assertFalse(stress[str(45 * MINUTE)]["experiments"]["1"]["improvement_persists"])
        criteria = self.signal_result["evidence_checklist"]["1"]["criteria"]
        self.assertEqual(criteria["4_survives_a_slower_feed"]["result"], "FAIL")


class HonestyTests(unittest.TestCase):
    def test_every_experiment_is_scored_on_the_same_rows(self) -> None:
        market = Market(0.002)
        result = run_ablation(spec(experiments=(0, 1, 2, 3), latency_stress_ms=()),
                              market.inputs(definitions=TRANSFERS_AND_TRADES))
        counts = {k: v["n_oos"] for k, v in result["experiments"].items()}
        self.assertEqual(set(counts), {"0", "1", "2", "3"})
        self.assertEqual(len(set(counts.values())), 1)
        self.assertEqual(result["data"]["common_samples"], 851)

    def test_ratios_that_are_undefined_in_quiet_hours_stay_out_of_the_model_and_the_exclusion_is_recorded(self) -> None:
        definitions = TRANSFERS_AND_TRADES + tuple(liquidation_definitions(("1h",)))
        market = Market(0.002)
        result = run_ablation(spec(experiments=(0, 1, 2, 3), latency_stress_ms=()), market.inputs(definitions=definitions))
        self.assertEqual(result["status"], "COMPLETED")  # quiet hours do not empty the sample
        excluded = result["data"]["excluded_from_model"]["features"]
        self.assertEqual(excluded, ["liquidation_imbalance_1h", "whale_buy_sell_ratio_1h"])
        used = set(result["experiments"]["3"]["features"])
        self.assertFalse(used & set(excluded))
        self.assertIn("whale_buy_usd_1h", used)  # the components are still there

    def test_an_unhealthy_provider_pauses_the_dependent_experiments_while_the_baseline_still_runs(self) -> None:
        def down(provider: str, at: int) -> HealthStatus:
            return HealthStatus(healthy=False, reasons=("consecutive_failures>=3",))

        market = Market(0.002)
        gated = dataclasses.replace(market.inputs(), health_gate=down)
        paused = run_ablation(spec(latency_stress_ms=()), gated)
        self.assertEqual(paused["status"], "INSUFFICIENT_DATA")
        self.assertTrue(paused["provider_health_gate_used"])
        self.assertIn("MISSING_PROVIDER_UNHEALTHY", " ".join(paused["reasons"]))
        fallback = run_ablation(spec(experiments=(0,), latency_stress_ms=()), gated)
        self.assertEqual(fallback["status"], "COMPLETED")
        self.assertEqual(set(fallback["experiments"]), {"0"})
        healthy = run_ablation(spec(latency_stress_ms=()), market.inputs())
        self.assertEqual(fallback["experiments"]["0"], healthy["experiments"]["0"])

    def test_the_reasons_for_missing_features_are_named(self) -> None:
        market = Market(0.002)
        result = run_ablation(spec(latency_stress_ms=()), market.inputs(coverage=CoverageIndex()))
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertIn("MISSING_NO_COVERAGE", result["data"]["external_missing_by_status"])
        self.assertIn("MISSING_NO_COVERAGE", " ".join(result["reasons"]))

    def test_missing_external_data_shrinks_the_sample_for_all_experiments(self) -> None:
        market = Market(0.002)
        half = full_coverage(T0 - 200 * HOUR, T0 + 560 * HOUR)  # the feed is declared complete for only part of the period
        result = run_ablation(spec(latency_stress_ms=(), min_common_samples=50), market.inputs(coverage=half))
        self.assertLess(result["data"]["common_samples"], 500)
        self.assertGreater(result["data"]["baseline_and_target_ok"], 800)
        self.assertEqual(result["experiments"]["0"]["n_oos"], result["experiments"]["1"]["n_oos"])

    def test_too_little_data_is_reported_as_insufficient_never_estimated(self) -> None:
        market = Market(0.002)
        for overrides in ({"min_common_samples": 5000}, {"min_effective_samples": 5000}):
            result = run_ablation(spec(**overrides), market.inputs())
            self.assertEqual(result["status"], "INSUFFICIENT_DATA")
            self.assertNotIn("experiments", result)
            self.assertTrue(result["reasons"])

    def test_a_missing_family_is_named_as_the_binding_constraint(self) -> None:
        market = Market(0.002)
        only_transfers = CoverageIndex([CoverageInterval("whale_alert", "transfers", T0 - 200 * HOUR, T0 + 2000 * HOUR, "d")])
        result = run_ablation(spec(experiments=(0, 1, 2, 3), latency_stress_ms=()),
                              market.inputs(definitions=TRANSFERS_AND_TRADES, coverage=only_transfers))
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["data"]["common_samples"], 0)
        self.assertIn("executed", " ".join(result["reasons"]))

    def test_placeholder_experiments_are_listed_and_never_run(self) -> None:
        result = run_ablation(spec(experiments=(0, 1, 4, 5), latency_stress_ms=()), Market(0.002).inputs())
        self.assertEqual(set(result["placeholders"]), {"4", "5"})
        self.assertEqual(set(result["experiments"]), {"0", "1"})

    def test_holdout_mode_scores_an_untouched_test_period_and_reports_validation_separately(self) -> None:
        result = run_ablation(
            spec(mode="holdout", walk_forward=None, latency_stress_ms=(),
                 holdout=HoldoutSpec(T0 + 600 * HOUR, T0 + 800 * HOUR)),
            Market(0.002).inputs(),
        )
        self.assertEqual([f["kind"] for f in result["folds"]], ["validation", "test"])
        self.assertIn("1", result["validation_diagnostics"])
        first_test = min(t for t in range(T0 + 800 * HOUR, T0 + 971 * HOUR, HOUR))
        self.assertGreaterEqual(first_test, T0 + 800 * HOUR)
        self.assertLess(result["experiments"]["1"]["n_oos"], 200)  # only the test period is scored

    def test_bad_specifications_are_refused(self) -> None:
        market = Market(0.002)
        bad = [
            spec(experiments=(1, 2)),
            spec(experiments=(0, 9)),
            spec(end=T0),
            spec(walk_forward=None),
            spec(mode="shuffled"),
            spec(mode="holdout", walk_forward=None),
        ]
        for candidate in bad:
            with self.assertRaises(ValueError):
                run_ablation(candidate, market.inputs())
        with self.assertRaises(ValueError):  # Experiment 2 would silently repeat Experiment 0
            run_ablation(spec(experiments=(0, 2)), market.inputs(definitions=TRANSFERS))


class ChecklistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.market = Market(0.002)
        cls.result = run_ablation(spec(), cls.market.inputs())

    def test_the_six_point_checklist_describes_and_never_promotes(self) -> None:
        checklist = self.result["evidence_checklist"]["1"]
        self.assertEqual(len(checklist["criteria"]), 6)
        self.assertFalse(checklist["all_criteria_passed"])
        self.assertIn("human decision", checklist["reminder"])
        for criterion in checklist["criteria"].values():
            self.assertIn(criterion["result"], {"PASS", "FAIL", "NOT_EVALUATED"})
        text = json.dumps(self.result).lower()
        for word in ("promote_to", "go_live", "place_order", "execute_trade"):
            self.assertNotIn(word, text)

    def test_the_first_criterion_passes_here_and_costs_are_labelled_as_assumptions(self) -> None:
        criteria = self.result["evidence_checklist"]["1"]["criteria"]
        self.assertEqual(criteria["1_beats_baseline_out_of_sample"]["result"], "PASS")
        self.assertEqual(self.result["spec"]["cost_bps_low"], 10.0)
        self.assertIn("Cost figures are assumptions", " ".join(self.result["notes"]))

    def test_trying_many_configurations_discounts_the_result(self) -> None:
        many = run_ablation(spec(preregistration={"runs_so_far": 60}), self.market.inputs())
        criteria = many["evidence_checklist"]["1"]["criteria"]
        self.assertEqual(criteria["5_survives_discounting_for_configurations_tried"]["result"], "FAIL")
        self.assertEqual(many["multiple_testing"]["runs_so_far_on_this_question"], 60)
        few = self.result["evidence_checklist"]["1"]["criteria"]["5_survives_discounting_for_configurations_tried"]
        self.assertEqual(few["result"], "PASS")

    def test_a_mechanism_must_have_been_written_down(self) -> None:
        none = run_ablation(spec(hypotheses=()), self.market.inputs())
        self.assertEqual(none["evidence_checklist"]["1"]["criteria"]["6_mechanism_statable_without_the_backtest"]["result"], "FAIL")
        self.assertEqual(self.result["evidence_checklist"]["1"]["criteria"]["6_mechanism_statable_without_the_backtest"]["result"], "PASS")


class ReproducibilityTests(unittest.TestCase):
    def test_the_same_inputs_give_byte_identical_results(self) -> None:
        market = Market(0.002)
        first = json.dumps(run_ablation(spec(), market.inputs()), sort_keys=True)
        second = json.dumps(run_ablation(spec(), market.inputs()), sort_keys=True)
        self.assertEqual(first, second)

    def test_the_dataset_fingerprint_changes_when_the_data_does(self) -> None:
        market = Market(0.002)
        base = dataset_fingerprint(market.inputs(), spec())
        self.assertEqual(base, dataset_fingerprint(market.inputs(), spec()))
        fewer = dataset_fingerprint(market.inputs(events=market.events[:-1]), spec())
        self.assertNotEqual(base["sha256"], fewer["sha256"])
        self.assertEqual(base["bars"], fewer["bars"])
        self.assertEqual(base["events"]["by_provider"], {"whale_alert": N})

    def test_the_seed_changes_uncertainty_but_never_the_forecasts(self) -> None:
        market = Market(0.002)
        a = run_ablation(spec(seed=1, latency_stress_ms=()), market.inputs())
        b = run_ablation(spec(seed=2, latency_stress_ms=()), market.inputs())
        self.assertEqual(a["experiments"]["0"], b["experiments"]["0"])
        self.assertEqual(gain(a)["mse_improvement"], gain(b)["mse_improvement"])
        self.assertNotEqual(gain(a)["ci95_low"], gain(b)["ci95_low"])

    def test_the_run_records_the_versions_and_settings_it_used(self) -> None:
        result = run_ablation(spec(), Market(0.002).inputs())
        self.assertEqual(result["ablation_version"], ABLATION_VERSION)
        self.assertEqual(result["model_version"], "ridge-1")
        self.assertEqual(result["provider_versions"], {"whale_alert": "test-fixture"})
        self.assertEqual(result["spec"]["seed"], 20260928)
        self.assertEqual(result["experiments"]["1"]["features"][:7], ["ret_1", "ret_4", "ret_24", "vol_24", "trend_24_96", "volume_ratio_24", "range_1"])


class RegistryBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.registry = ExperimentRegistry(self._tmp.name)
        self.market = Market(0.002)

    def record(self, **spec_overrides):
        return run_and_record(self.registry, spec(**spec_overrides), self.market.inputs(), created_at=T0)

    def test_a_run_is_written_with_everything_needed_to_reproduce_it(self) -> None:
        run = self.record()
        record = self.registry.load(run.run_id)
        self.assertEqual(record["status"], "COMPLETED")
        self.assertEqual(record["strategy_name"], STRATEGY_NAME)
        self.assertEqual(record["dataset_version"]["sha256"], run.result["dataset"]["sha256"])
        self.assertEqual(record["eligible_symbols"], ["BTCUSDT"])
        self.assertEqual((record["starting_holdings"], record["starting_cash"], record["external_contributions"]), ([], {}, []))
        self.assertEqual(record["execution_settings"]["kind"], "predictive_research")
        self.assertIn("ASSUMPTION", record["fee_assumptions"]["source"])
        self.assertEqual(record["config"]["hypotheses"], ["A"])
        self.assertIn("features", record["config"])
        self.assertIn("whale_alert", record["tool_versions"])
        for name in ("record.json", "metrics.json", "config.resolved.json", "ablation_result.json"):
            self.assertTrue((Path(run.run_dir) / name).exists(), name)

    def test_runs_are_never_overwritten_and_each_counts_as_a_run_on_the_question(self) -> None:
        first, second = self.record(), self.record()
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertEqual(len(self.registry.list_runs()), 2)
        self.assertEqual(count_prior_runs(self.registry, experiment_name="probe"), 2)
        self.assertIn("RUNS SO FAR ON THIS QUESTION: 1", self.registry.load(second.run_id)["description"])
        self.assertEqual(len(runs_for_hypothesis(self.registry, "A")), 2)
        self.assertEqual(runs_for_hypothesis(self.registry, "B"), [])

    def test_a_run_that_could_not_answer_is_kept_as_failed_with_the_reason(self) -> None:
        run = self.record(min_common_samples=5000)
        record = self.registry.load(run.run_id)
        self.assertEqual(record["status"], "FAILED")
        self.assertIn("INSUFFICIENT_DATA", record["error"])
        self.assertEqual(count_prior_runs(self.registry, experiment_name="probe"), 1)

    def test_a_run_that_crashed_is_kept_with_its_traceback(self) -> None:
        empty = AblationInputs(events=[], coverage=CoverageIndex(), bars=[])
        run = run_and_record(self.registry, spec(), empty, created_at=T0)
        record = self.registry.load(run.run_id)
        self.assertEqual(record["status"], "FAILED")
        self.assertIn("ValueError", record["error"])
        self.assertIn("Traceback", record["traceback"])
        self.assertIsNone(run.result)

    def test_the_preregistration_template_is_filled_from_the_spec(self) -> None:
        text = preregistration_text(spec(preregistration={"in_sample": "2024", "success": "beats baseline", "runs_so_far": 3}))
        for label in ("HYPOTHESIS:", "IN-SAMPLE:  2024", "OUT-OF-SAMPLE:", "BASELINE:", "SUCCESS:    beats baseline",
                      "RUNS SO FAR ON THIS QUESTION: 3"):
            self.assertIn(label, text)


if __name__ == "__main__":
    unittest.main()
