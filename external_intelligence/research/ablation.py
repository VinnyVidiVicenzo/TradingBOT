"""Feature-family ablation: does an external variable add anything beyond market data?

The question is never "did price move after a whale event?" It is: *after
accounting for what price, volume and volatility already show, did the external
variable improve a forecast made only from information available at the time?*

Experiments (each adds columns to the same fixed, plain forecaster):

    0  market data only
    1  market data + transfer features        (Whale Alert-style, incl. stablecoin issuance)
    2  market data + executed-trade features  (executed trades, liquidations, funding / OI)
    3  market data + both
    4  + entity attribution          -- placeholder, not available
    5  + aggregate on-chain regime   -- placeholder, not available

How it stays honest
-------------------
* **One sample, all experiments.** Every experiment is scored on exactly the same
  rows -- those where *every* feature any experiment needs is present. Otherwise an
  experiment could look better merely by being tested on easier rows. Missing
  external data therefore shrinks the sample for all of them, and if it shrinks it
  below a stated minimum the run reports ``INSUFFICIENT_DATA`` instead of numbers.
* **Chronological, purged evaluation** (see ``splits``); nothing is shuffled.
* **The benchmark is the prevailing mean** and the baseline model is the same
  ridge model on market features only, so a gain is a gain from the *added* columns.
* **Overlap is priced in.** Forecast horizons longer than the sampling step make
  neighbouring rows share outcomes. Uncertainty comes from a moving-block
  bootstrap with blocks at least one horizon long, and the effective sample size
  is stated.
* **A family of comparisons is corrected** (Holm), and the count of earlier runs
  on the same question further discounts the result (see the checklist).
* **Nothing is promoted.** The output includes a six-point checklist taken from
  ``docs/trading-bot/EXPERIMENTS.md``. It describes; a human decides.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from crypto_research.models import MarketBar

from ..base.coverage import CoverageIndex
from ..base.health import HealthStatus
from ..events import ExternalEvent
from ..features.definitions import FeatureDefinition, Metric, default_feature_definitions
from ..features.pipeline import FeaturePipeline, InMemoryEventSource
from .baseline import BASELINE_FEATURES, BaselineFeatureBuilder
from .hypotheses import HYPOTHESES
from .linear import MODEL_VERSION, PrevailingMean, RidgeModel, mse
from .price import BarPriceSource, forward_return
from .splits import (
    Fold,
    HoldoutSpec,
    WalkForwardSpec,
    assert_no_leakage,
    holdout_folds,
    walk_forward_folds,
)
from .stats import block_bootstrap_mean, effective_samples, holm_adjust

ABLATION_VERSION = "1"

EXPERIMENT_LABELS = {
    0: "market data only",
    1: "market data + transfer features",
    2: "market data + executed-trade, liquidation and derivatives features",
    3: "market data + both feature families",
}
EXPERIMENT_FAMILIES: Mapping[int, tuple[str, ...]] = {
    0: (),
    1: ("transfer", "stablecoin"),
    2: ("executed", "liquidation", "derivatives"),
    3: ("transfer", "stablecoin", "executed", "liquidation", "derivatives"),
}
PLACEHOLDER_EXPERIMENTS = {
    4: "entity attribution: not available (needs point-in-time labels from a provider that is not integrated)",
    5: "aggregate on-chain regime features: not available (no such provider is integrated)",
}
CONCENTRATION_BLOCKS = 8
CONCENTRATION_LIMIT = 0.6
# Ratios and imbalances are undefined in a window with nothing on one side. Requiring them
# would keep only the busy hours and bias the sample, so the model uses their components
# (buys, sells, long and short liquidations) instead. They remain available to describe.
MODEL_EXCLUDED_METRICS = (Metric.RATIO, Metric.IMBALANCE)


@dataclass(frozen=True)
class AblationSpec:
    name: str
    asset: str
    market_symbol: str
    interval: str
    start: int
    end: int
    step_ms: int
    horizon_ms: int
    mode: str = "walk_forward"  # or "holdout"
    walk_forward: WalkForwardSpec | None = None
    holdout: HoldoutSpec | None = None
    experiments: tuple[int, ...] = (0, 1, 2, 3)
    alpha: float = 10.0
    allow_estimated: bool = False
    latency_stress_ms: tuple[int, ...] = (900_000,)
    # Round-trip cost assumptions in basis points. Placeholders, labelled as such.
    cost_bps_low: float = 10.0
    cost_bps_high: float = 60.0
    bootstrap_iterations: int = 1000
    seed: int = 20260928
    min_common_samples: int = 200
    min_effective_samples: int = 30
    hypotheses: tuple[str, ...] = ()
    preregistration: Mapping[str, Any] = field(default_factory=dict)
    notes: str = ""

    def validate(self) -> None:
        if self.end <= self.start or self.step_ms <= 0 or self.horizon_ms <= 0:
            raise ValueError("need start < end and positive step and horizon")
        if self.mode == "walk_forward" and self.walk_forward is None:
            raise ValueError("walk_forward mode needs a WalkForwardSpec")
        if self.mode == "holdout" and self.holdout is None:
            raise ValueError("holdout mode needs a HoldoutSpec")
        if self.mode not in ("walk_forward", "holdout"):
            raise ValueError(f"unknown mode {self.mode!r}")
        if 0 not in self.experiments:
            raise ValueError("Experiment 0 (market data only) is the baseline and must be included")
        unknown = [k for k in self.experiments if k not in EXPERIMENT_FAMILIES and k not in PLACEHOLDER_EXPERIMENTS]
        if unknown:
            raise ValueError(f"unknown experiment number(s): {unknown}")

    @property
    def overlap_factor(self) -> float:
        return max(1.0, self.horizon_ms / self.step_ms)

    def to_config(self) -> dict[str, Any]:
        return {
            "name": self.name, "asset": self.asset, "market_symbol": self.market_symbol,
            "interval": self.interval, "start": self.start, "end": self.end,
            "step_ms": self.step_ms, "horizon_ms": self.horizon_ms, "mode": self.mode,
            "walk_forward": None if self.walk_forward is None else {
                "initial_train_ms": self.walk_forward.initial_train_ms,
                "test_block_ms": self.walk_forward.test_block_ms},
            "holdout": None if self.holdout is None else {
                "train_end": self.holdout.train_end, "validation_end": self.holdout.validation_end},
            "experiments": list(self.experiments), "alpha": self.alpha,
            "allow_estimated_receipts": self.allow_estimated,
            "latency_stress_ms": list(self.latency_stress_ms),
            "cost_bps_low": self.cost_bps_low, "cost_bps_high": self.cost_bps_high,
            "bootstrap_iterations": self.bootstrap_iterations, "seed": self.seed,
            "min_common_samples": self.min_common_samples,
            "min_effective_samples": self.min_effective_samples,
            "hypotheses": list(self.hypotheses), "preregistration": dict(self.preregistration),
            "notes": self.notes, "ablation_version": ABLATION_VERSION, "model_version": MODEL_VERSION,
        }


@dataclass(frozen=True)
class AblationInputs:
    events: Sequence[ExternalEvent]
    coverage: CoverageIndex
    bars: Sequence[MarketBar]
    definitions: Sequence[FeatureDefinition] | None = None
    providers: Mapping[str, str] | None = None
    provider_versions: Mapping[str, str] = field(default_factory=dict)
    # Optional live-style gate: an unhealthy provider's features turn missing, which
    # pauses the experiments that depend on them (Experiment 0 still runs on its own).
    health_gate: Callable[[str, int], HealthStatus] | None = None


@dataclass(slots=True)
class _Sample:
    time: int
    baseline: Mapping[str, float]
    external: dict[str, float | None]
    target: float


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def dataset_fingerprint(inputs: AblationInputs, spec: AblationSpec) -> dict[str, Any]:
    """A hash of exactly what a run was given, so a rerun can prove it saw the same data."""
    event_ids = sorted(e.event_id for e in inputs.events)
    bars = sorted(inputs.bars, key=lambda b: b.open_time)
    bar_blob = "|".join(f"{b.open_time}:{b.close}" for b in bars)
    events_hash = hashlib.sha256("|".join(event_ids).encode("utf-8")).hexdigest()
    bars_hash = hashlib.sha256(bar_blob.encode("utf-8")).hexdigest()
    combined = hashlib.sha256(f"{events_hash}{bars_hash}".encode("utf-8")).hexdigest()
    providers: dict[str, int] = {}
    for event in inputs.events:
        providers[event.provider] = providers.get(event.provider, 0) + 1
    return {
        "sha256": combined,
        "events": {"count": len(event_ids), "sha256": events_hash, "by_provider": dict(sorted(providers.items()))},
        "bars": {
            "symbol": spec.market_symbol, "interval": spec.interval, "count": len(bars),
            "first_open_time": bars[0].open_time if bars else None,
            "last_open_time": bars[-1].open_time if bars else None, "sha256": bars_hash,
        },
    }


def run_ablation(spec: AblationSpec, inputs: AblationInputs) -> dict[str, Any]:
    spec.validate()
    definitions = list(inputs.definitions) if inputs.definitions is not None else default_feature_definitions()
    experiments = sorted(set(spec.experiments))
    runnable = [k for k in experiments if k in EXPERIMENT_FAMILIES]
    model_definitions = [d for d in definitions if d.metric not in MODEL_EXCLUDED_METRICS]
    excluded = sorted(d.name for d in definitions if d.metric in MODEL_EXCLUDED_METRICS)
    feature_sets = {
        k: [d.name for d in model_definitions if d.family in EXPERIMENT_FAMILIES[k]] for k in runnable
    }
    for k in runnable:
        if k != 0 and not feature_sets[k]:
            raise ValueError(
                f"Experiment {k} adds no features under these definitions; it would silently "
                "repeat Experiment 0"
            )
    external_needed = sorted({name for k in runnable for name in feature_sets[k]})

    result: dict[str, Any] = {
        "ablation_version": ABLATION_VERSION,
        "model_version": MODEL_VERSION,
        "spec": spec.to_config(),
        "dataset": dataset_fingerprint(inputs, spec),
        "provider_versions": dict(inputs.provider_versions),
        "provider_health_gate_used": inputs.health_gate is not None,
        "experiment_labels": {str(k): EXPERIMENT_LABELS[k] for k in runnable},
        "placeholders": {str(k): PLACEHOLDER_EXPERIMENTS[k] for k in experiments if k in PLACEHOLDER_EXPERIMENTS},
        "status": "COMPLETED",
        "reasons": [],
    }

    price = BarPriceSource(inputs.bars)
    baseline_builder = BaselineFeatureBuilder(inputs.bars)
    grid = list(range(spec.start, spec.end + 1, spec.step_ms))
    rows, data = _baseline_and_targets(grid, baseline_builder, price, spec)
    external, missing_by_status = _external_features(
        spec, inputs, definitions, external_needed, [r[0] for r in rows], 0
    )
    family_counts = _family_counts(rows, external, model_definitions)
    samples = _complete_cases(rows, external, external_needed)
    data.update(
        {
            "external_features_required": external_needed,
            "external_missing_by_status": missing_by_status,
            "excluded_from_model": {
                "features": excluded,
                "reason": "undefined when a window has nothing on one side; their components are used instead",
            },
            "family_complete_counts": family_counts,
            "common_samples": len(samples),
            "effective_samples": effective_samples(len(samples), spec.overlap_factor),
            "overlap_factor": spec.overlap_factor,
            "first_sample_time": samples[0].time if samples else None,
            "last_sample_time": samples[-1].time if samples else None,
        }
    )
    result["data"] = data

    reasons = _insufficiency_reasons(spec, len(samples), data, family_counts)
    if reasons:
        result["status"] = "INSUFFICIENT_DATA"
        result["reasons"] = reasons
        return result

    evaluation = _evaluate(spec, samples, feature_sets, runnable)
    if evaluation is None:
        result["status"] = "INSUFFICIENT_DATA"
        result["reasons"] = ["no evaluable chronological folds for this sample and split"]
        return result
    result["experiments"] = evaluation["experiments"]
    result["validation_diagnostics"] = evaluation["validation"]
    result["folds"] = evaluation["folds"]

    stress: dict[str, Any] = {}
    for delay in spec.latency_stress_ms:
        stress[str(delay)] = _stress(spec, inputs, definitions, rows, feature_sets, runnable, external_needed, delay)
    result["latency_stress"] = stress

    tests = [k for k in runnable if k != 0]
    runs_so_far = int(spec.preregistration.get("runs_so_far", 0) or 0)
    result["multiple_testing"] = {
        "comparisons_in_this_run": len(tests),
        "runs_so_far_on_this_question": runs_so_far,
        "correction_in_run": "Holm across the comparisons with Experiment 0",
        "correction_across_runs": "raw p multiplied by (runs so far + comparisons in this run)",
    }
    result["evidence_checklist"] = {
        str(k): _checklist(spec, result["experiments"][str(k)], stress, k, runs_so_far, len(tests))
        for k in tests
    }
    result["notes"] = [
        "Nothing here promotes a strategy. The checklist describes what was and was not shown.",
        "External features are missing wherever the feed was not declared complete; the sample "
        "shrinks accordingly for every experiment.",
        "Cost figures are assumptions (round-trip basis points), not measurements.",
    ]
    return result


# --------------------------------------------------------------------------
# building the sample
# --------------------------------------------------------------------------


def _baseline_and_targets(
    grid: Sequence[int], baseline: BaselineFeatureBuilder, price: BarPriceSource, spec: AblationSpec
) -> tuple[list[tuple[int, Mapping[str, float], float]], dict[str, Any]]:
    rows: list[tuple[int, Mapping[str, float], float]] = []
    incomplete = 0
    target_reasons: dict[str, int] = {}
    for moment in grid:
        features = baseline.features_at(moment)
        if any(features[name] is None for name in BASELINE_FEATURES):
            incomplete += 1
            continue
        simple, reason = forward_return(price, moment, spec.horizon_ms)
        if simple is None or simple <= -1.0:
            key = reason or "non_positive_price_ratio"
            target_reasons[key] = target_reasons.get(key, 0) + 1
            continue
        rows.append((moment, {n: float(features[n]) for n in BASELINE_FEATURES}, math.log1p(simple)))
    return rows, {
        "grid_points": len(grid),
        "baseline_incomplete": incomplete,
        "target_unavailable": dict(sorted(target_reasons.items())),
        "baseline_and_target_ok": len(rows),
    }


def _external_features(
    spec: AblationSpec, inputs: AblationInputs, definitions: Sequence[FeatureDefinition],
    names: Sequence[str], times: Sequence[int], extra_delay_ms: int,
) -> tuple[dict[int, dict[str, float | None]], dict[str, int]]:
    """Feature values per decision time, and how often each kind of "missing" occurred."""
    if not names:
        return {t: {} for t in times}, {}
    wanted = [d for d in definitions if d.name in set(names)]
    pipeline = FeaturePipeline(
        InMemoryEventSource(inputs.events), inputs.coverage, definitions=wanted,
        providers=inputs.providers, allow_estimated=spec.allow_estimated,
        extra_delay_ms=extra_delay_ms, health_gate=inputs.health_gate,
    )
    out: dict[int, dict[str, float | None]] = {}
    missing: dict[str, int] = {}
    for moment in times:
        snapshot = pipeline.compute(spec.asset, moment)
        row: dict[str, float | None] = {}
        for name in names:
            value = snapshot.get(name)
            row[name] = value.as_float()
            if value.is_missing:
                missing[value.status.value] = missing.get(value.status.value, 0) + 1
        out[moment] = row
    return out, dict(sorted(missing.items()))


def _family_counts(
    rows: Sequence[tuple[int, Mapping[str, float], float]],
    external: Mapping[int, Mapping[str, float | None]],
    definitions: Sequence[FeatureDefinition],
) -> dict[str, int]:
    by_family: dict[str, list[str]] = {}
    for definition in definitions:
        by_family.setdefault(definition.family, []).append(definition.name)
    counts: dict[str, int] = {}
    for family, names in sorted(by_family.items()):
        counts[family] = sum(
            1 for moment, _, _ in rows
            if all(external.get(moment, {}).get(n) is not None for n in names)
        )
    return counts


def _complete_cases(
    rows: Sequence[tuple[int, Mapping[str, float], float]],
    external: Mapping[int, Mapping[str, float | None]], names: Sequence[str],
) -> list[_Sample]:
    samples: list[_Sample] = []
    for moment, baseline, target in rows:
        values = external.get(moment, {})
        if all(values.get(n) is not None for n in names):
            samples.append(_Sample(moment, baseline, {n: float(values[n]) for n in names}, target))  # type: ignore[arg-type]
    return samples


def _insufficiency_reasons(
    spec: AblationSpec, n_common: int, data: Mapping[str, Any], family_counts: Mapping[str, int]
) -> list[str]:
    reasons: list[str] = []
    if n_common < spec.min_common_samples:
        reasons.append(f"only {n_common} complete samples; at least {spec.min_common_samples} are required")
        if family_counts:
            binding = min(family_counts, key=lambda f: family_counts[f])
            reasons.append(f"the scarcest feature family is '{binding}' with {family_counts[binding]} complete samples")
        missing = data.get("external_missing_by_status") or {}
        if missing:
            detail = ", ".join(f"{name}={count}" for name, count in missing.items())
            reasons.append(f"external feature readings were missing for these reasons: {detail}")
    if data["effective_samples"] < spec.min_effective_samples:
        reasons.append(
            f"effective sample size {data['effective_samples']:.1f} (after overlap) is below "
            f"{spec.min_effective_samples}"
        )
    return reasons


# --------------------------------------------------------------------------
# evaluation
# --------------------------------------------------------------------------


def _folds(spec: AblationSpec, times: Sequence[int]) -> list[Fold]:
    if spec.mode == "holdout":
        assert spec.holdout is not None
        return holdout_folds(times, spec.horizon_ms, spec.holdout)
    assert spec.walk_forward is not None
    return walk_forward_folds(times, spec.horizon_ms, spec.walk_forward)


def _matrix(samples: Sequence[_Sample], indices: Sequence[int], external_names: Sequence[str]) -> list[list[float]]:
    rows = []
    for i in indices:
        s = samples[i]
        rows.append([s.baseline[n] for n in BASELINE_FEATURES] + [s.external[n] for n in external_names])  # type: ignore[misc]
    return rows


def _evaluate(
    spec: AblationSpec, samples: Sequence[_Sample], feature_sets: Mapping[int, Sequence[str]],
    runnable: Sequence[int],
) -> dict[str, Any] | None:
    times = [s.time for s in samples]
    folds = _folds(spec, times)
    for fold in folds:
        assert_no_leakage(times, spec.horizon_ms, fold)
    usable = [f for f in folds if f.train and f.test]
    reporting = [f for f in usable if f.kind in ("walk_forward", "test")]
    diagnostic = [f for f in usable if f.kind == "validation"]
    if not reporting:
        return None

    pooled: dict[int, dict[str, Any]] = {}
    validation: dict[str, Any] = {}
    for k in runnable:
        pooled[k] = _run_folds(spec, samples, feature_sets[k], reporting)
        if diagnostic:
            validation[str(k)] = _metrics(_run_folds(spec, samples, feature_sets[k], diagnostic), spec)
    experiments: dict[str, Any] = {}
    base = pooled[0]
    p_raw: dict[int, float] = {}
    for k in runnable:
        entry = _metrics(pooled[k], spec)
        entry["label"] = EXPERIMENT_LABELS[k]
        entry["features"] = list(BASELINE_FEATURES) + list(feature_sets[k])
        if k != 0:
            comparison = _compare(spec, base, pooled[k], k)
            entry["vs_experiment_0"] = comparison
            p_raw[k] = comparison["p_one_sided"] if comparison["p_one_sided"] is not None else 1.0
        experiments[str(k)] = entry
    if p_raw:
        keys = sorted(p_raw)
        adjusted = holm_adjust([p_raw[k] for k in keys])
        for key, value in zip(keys, adjusted):
            experiments[str(key)]["vs_experiment_0"]["p_holm"] = value
    return {
        "experiments": experiments,
        "validation": validation,
        "folds": [
            {"name": f.name, "kind": f.kind, "train": len(f.train), "test": len(f.test)} for f in folds
        ],
    }


def _run_folds(
    spec: AblationSpec, samples: Sequence[_Sample], external_names: Sequence[str], folds: Sequence[Fold]
) -> dict[str, Any]:
    predicted: list[float] = []
    benchmark: list[float] = []
    actual: list[float] = []
    order: list[int] = []
    for fold in folds:
        y_train = [samples[i].target for i in fold.train]
        model = RidgeModel(spec.alpha).fit(_matrix(samples, fold.train, external_names), y_train)
        predicted += model.predict(_matrix(samples, fold.test, external_names))
        benchmark += PrevailingMean().fit(y_train).predict(len(fold.test))
        actual += [samples[i].target for i in fold.test]
        order += list(fold.test)
    return {"predicted": predicted, "benchmark": benchmark, "actual": actual, "order": order}


def _metrics(pooled: Mapping[str, Any], spec: AblationSpec) -> dict[str, Any]:
    y, p, b = pooled["actual"], pooled["predicted"], pooled["benchmark"]
    n = len(y)
    mse_model = mse(y, p)
    mse_bench = mse(y, b)
    deviations = [(pi - bi, yi - bi) for yi, pi, bi in zip(y, p, b) if pi != bi and yi != bi]
    hits = sum(1 for dp, dy in deviations if (dp > 0) == (dy > 0))
    long_ = [yi for yi, pi in zip(y, p) if pi > 0]
    flat = [yi for yi, pi in zip(y, p) if pi <= 0]
    edge = (statistics.fmean(long_) - statistics.fmean(flat)) * 10_000 if long_ and flat else None
    return {
        "n_oos": n,
        "n_effective": effective_samples(n, spec.overlap_factor),
        "mse_model": mse_model,
        "mse_prevailing_mean": mse_bench,
        "r2_vs_prevailing_mean": 1 - mse_model / mse_bench if mse_bench > 0 else None,
        "directional_accuracy": hits / len(deviations) if deviations else None,
        "edge_bps": edge,
        "share_predicted_positive": len(long_) / n if n else None,
    }


def _loss_differential(base: Mapping[str, Any], other: Mapping[str, Any]) -> list[float]:
    """Positive where the model with extra features had the smaller squared error."""
    if base["order"] != other["order"]:
        raise ValueError("experiments were not scored on identical rows")
    return [
        (yb - pb) ** 2 - (yo - po) ** 2
        for yb, pb, yo, po in zip(base["actual"], base["predicted"], other["actual"], other["predicted"])
    ]


def _compare(spec: AblationSpec, base: Mapping[str, Any], other: Mapping[str, Any], k: int) -> dict[str, Any]:
    d = _loss_differential(base, other)
    block = max(1, math.ceil(spec.horizon_ms / spec.step_ms))
    boot = block_bootstrap_mean(d, block_length=block, iterations=spec.bootstrap_iterations, seed=spec.seed + 101 * k)
    mse_base = mse(base["actual"], base["predicted"])
    mse_other = mse(other["actual"], other["predicted"])
    base_metrics, other_metrics = _metrics(base, spec), _metrics(other, spec)
    return {
        "mse_improvement": mse_base - mse_other,
        "mse_improvement_pct": (mse_base - mse_other) / mse_base * 100 if mse_base > 0 else None,
        "mean_loss_differential": boot["mean"],
        "ci95_low": boot["ci_low"],
        "ci95_high": boot["ci_high"],
        "p_one_sided": boot["p_one_sided"],
        "bootstrap_block_length": boot["block_length"],
        "bootstrap_insufficient": boot["insufficient"],
        "incremental_edge_bps": (
            other_metrics["edge_bps"] - base_metrics["edge_bps"]
            if other_metrics["edge_bps"] is not None and base_metrics["edge_bps"] is not None else None
        ),
        "share_of_improvement_from_best_two_periods": _concentration(d),
    }


def _concentration(d: Sequence[float]) -> float | None:
    """How much of the total gain came from the best two of eight chronological blocks."""
    if len(d) < CONCENTRATION_BLOCKS * 2 or sum(d) <= 0:
        return None
    size = len(d) // CONCENTRATION_BLOCKS
    sums = [sum(d[i * size:(i + 1) * size if i < CONCENTRATION_BLOCKS - 1 else len(d)])
            for i in range(CONCENTRATION_BLOCKS)]
    positive = sorted((s for s in sums if s > 0), reverse=True)
    if not positive:
        return None
    return sum(positive[:2]) / sum(positive)


# --------------------------------------------------------------------------
# sensitivity to being slower, and the checklist
# --------------------------------------------------------------------------


def _stress(
    spec: AblationSpec, inputs: AblationInputs, definitions: Sequence[FeatureDefinition],
    rows: Sequence[tuple[int, Mapping[str, float], float]], feature_sets: Mapping[int, Sequence[str]],
    runnable: Sequence[int], external_needed: Sequence[str], extra_delay_ms: int,
) -> dict[str, Any]:
    external, _ = _external_features(spec, inputs, definitions, external_needed, [r[0] for r in rows], extra_delay_ms)
    samples = _complete_cases(rows, external, external_needed)
    if len(samples) < spec.min_common_samples:
        return {"status": "INSUFFICIENT_DATA", "common_samples": len(samples)}
    evaluation = _evaluate(spec, samples, feature_sets, runnable)
    if evaluation is None:
        return {"status": "INSUFFICIENT_DATA", "common_samples": len(samples)}
    out: dict[str, Any] = {"status": "COMPLETED", "common_samples": len(samples), "experiments": {}}
    for k in runnable:
        if k == 0:
            continue
        comparison = evaluation["experiments"][str(k)]["vs_experiment_0"]
        out["experiments"][str(k)] = {
            "mse_improvement": comparison["mse_improvement"],
            "improvement_persists": comparison["mse_improvement"] > 0,
        }
    return out


def _checklist(
    spec: AblationSpec, entry: Mapping[str, Any], stress: Mapping[str, Any], k: int,
    runs_so_far: int, comparisons: int,
) -> dict[str, Any]:
    comparison = entry["vs_experiment_0"]
    p_holm = comparison.get("p_holm")
    p_raw = comparison["p_one_sided"]
    criteria: dict[str, dict[str, Any]] = {}

    def verdict(passed: bool | None) -> str:
        return "NOT_EVALUATED" if passed is None else ("PASS" if passed else "FAIL")

    improved = comparison["mse_improvement"] > 0
    unresolved = p_holm is None or comparison["bootstrap_insufficient"]
    pct = comparison["mse_improvement_pct"]
    criteria["1_beats_baseline_out_of_sample"] = {
        "result": verdict(None if unresolved else (improved and p_holm < 0.05)),
        "detail": (
            f"MSE improvement {'n/a' if pct is None else f'{pct:.3f}'}%, Holm-adjusted one-sided p = "
            f"{'n/a (too few samples for the bootstrap)' if unresolved else f'{p_holm:.4f}'}"
        ),
    }
    edge, inc = entry["edge_bps"], comparison["incremental_edge_bps"]
    if edge is None or inc is None:
        cost_passed = None
    else:
        cost_passed = inc > 0 and edge > spec.cost_bps_high and edge > spec.cost_bps_low
    criteria["2_margin_exceeds_cost_drag_at_low_and_high"] = {
        "result": verdict(cost_passed),
        "detail": f"edge {edge} bps, incremental {inc} bps, versus assumed costs {spec.cost_bps_low} and {spec.cost_bps_high} bps",
    }
    share = comparison["share_of_improvement_from_best_two_periods"]
    criteria["3_not_concentrated_in_one_or_two_periods"] = {
        "result": verdict(None if share is None else share < CONCENTRATION_LIMIT),
        "detail": f"best two of {CONCENTRATION_BLOCKS} periods account for {share} of the gain (limit {CONCENTRATION_LIMIT})",
    }
    stress_rows = [s["experiments"].get(str(k)) for s in stress.values() if s.get("status") == "COMPLETED"]
    criteria["4_survives_a_slower_feed"] = {
        "result": verdict(None if not stress_rows else all(r and r["improvement_persists"] for r in stress_rows)),
        "detail": f"tested extra delays (ms): {list(spec.latency_stress_ms)}",
    }
    n_tests = runs_so_far + comparisons
    discounted = None if p_raw is None else min(1.0, p_raw * n_tests)
    criteria["5_survives_discounting_for_configurations_tried"] = {
        "result": verdict(None if discounted is None else (improved and discounted < 0.05)),
        "detail": f"raw p {p_raw} x {n_tests} configurations (runs so far {runs_so_far} + comparisons {comparisons}) = {discounted}",
    }
    mechanisms = [HYPOTHESES[h].mechanism for h in spec.hypotheses if h in HYPOTHESES]
    criteria["6_mechanism_statable_without_the_backtest"] = {
        "result": verdict(bool(mechanisms) and all(m.strip() for m in mechanisms)),
        "detail": (
            f"linked hypotheses: {list(spec.hypotheses)}; this only checks that a mechanism was "
            "written down in advance, whether it is convincing is a human judgement"
        ),
    }
    return {
        "criteria": criteria,
        "all_criteria_passed": all(c["result"] == "PASS" for c in criteria.values()),
        "reminder": "Even if every criterion passes, promotion is a human decision; this is not it.",
    }
