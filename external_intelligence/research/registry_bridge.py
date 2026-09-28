"""Record ablation runs in the engine's own append-only experiment registry.

An ablation run is an ordinary ``RunRecord``: same directory layout, same index,
same two rules (nothing is overwritten; failures are kept). Fields that describe
a simulated portfolio -- holdings, cash, contributions -- are present and empty,
with ``execution_settings`` saying plainly that this is predictive research in
which no orders, holdings or cash are simulated.

A run that raised, and a run that could not answer the question for lack of data,
are both recorded as FAILED with the reason. Both still count as a run on the
question, which is what makes "the best of N" visible instead of forgettable.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from crypto_research.experiments.registry import (
    ExperimentRegistry,
    RunRecord,
    RunStatus,
    config_hash,
    git_state,
)
from crypto_research.timeutil import now_ms

from .. import __version__
from .ablation import (
    ABLATION_VERSION,
    EXPERIMENT_FAMILIES,
    MODEL_EXCLUDED_METRICS,
    AblationInputs,
    AblationSpec,
    run_ablation,
)
from .hypotheses import count_prior_runs
from .linear import MODEL_VERSION

STRATEGY_NAME = "external_intelligence.ablation"


@dataclass(slots=True)
class RecordedRun:
    run_id: str
    run_dir: Path
    status: RunStatus
    result: dict[str, Any] | None
    error: str | None


def preregistration_text(spec: AblationSpec) -> str:
    """The pre-registration template from EXPERIMENTS.md, filled in from the spec."""
    pre = spec.preregistration
    lines = [
        f"HYPOTHESIS: {pre.get('hypothesis', ', '.join(spec.hypotheses) or '(none stated)')}",
        f"IN-SAMPLE:  {pre.get('in_sample', '(not stated)')}",
        f"OUT-OF-SAMPLE: {pre.get('out_of_sample', '(not stated)')}",
        f"BASELINE:   {pre.get('baseline', 'Experiment 0: market data only, same model and sample')}",
        f"SUCCESS:    {pre.get('success', '(not stated)')}",
        f"RUNS SO FAR ON THIS QUESTION: {pre.get('runs_so_far', 0)}",
    ]
    if spec.notes:
        lines.append(f"NOTES: {spec.notes}")
    return "\n".join(lines)


def run_and_record(
    registry: ExperimentRegistry,
    spec: AblationSpec,
    inputs: AblationInputs,
    *,
    created_at: int | None = None,
) -> RecordedRun:
    """Run an ablation and write it to the registry, whatever the outcome."""
    if "runs_so_far" not in spec.preregistration:
        prior = count_prior_runs(registry, experiment_name=spec.name)
        spec = dataclasses.replace(spec, preregistration={**spec.preregistration, "runs_so_far": prior})
    stamp = created_at if created_at is not None else now_ms()
    result: dict[str, Any] | None = None
    error: str | None = None
    trace: str | None = None
    try:
        result = run_ablation(spec, inputs)
        if result["status"] != "COMPLETED":
            error = f"{result['status']}: " + "; ".join(result["reasons"])
    except Exception as exc:  # recorded, not swallowed: the record carries the traceback
        error = f"{type(exc).__name__}: {exc}"
        trace = traceback.format_exc()

    config = spec.to_config()
    config["features"] = _features_by_experiment(inputs, spec)
    config["providers"] = dict(inputs.providers) if inputs.providers is not None else "defaults"
    digest = config_hash(config)
    commit, dirty = git_state(Path.cwd())
    warnings = list(result.get("notes", [])) if result else []
    if result and result["reasons"]:
        warnings = list(result["reasons"]) + warnings
    record = RunRecord(
        run_id=registry.allocate_run_id(digest, at=stamp),
        created_at=stamp,
        status=RunStatus.COMPLETED if error is None else RunStatus.FAILED,
        experiment_name=spec.name,
        description=preregistration_text(spec),
        strategy_name=STRATEGY_NAME,
        strategy_version=ABLATION_VERSION,
        strategy_params={
            "model": MODEL_VERSION, "alpha": spec.alpha, "experiments": list(spec.experiments),
            "mode": spec.mode, "horizon_ms": spec.horizon_ms, "step_ms": spec.step_ms,
        },
        config=config,
        config_sha256=digest,
        dataset_version=result["dataset"] if result else None,
        eligible_symbols=[spec.market_symbol],
        starting_holdings=[],
        starting_cash={},
        external_contributions=[],
        fee_assumptions={
            "round_trip_cost_bps_low": spec.cost_bps_low, "round_trip_cost_bps_high": spec.cost_bps_high,
            "source": "ASSUMPTION -- placeholders, not measured",
        },
        spread_assumptions={"used": False, "note": "predictive research: no fills are simulated"},
        slippage_assumptions={"used": False, "note": "predictive research: no fills are simulated"},
        latency_assumptions={
            "allow_estimated_receipts": spec.allow_estimated,
            "latency_stress_extra_delay_ms": list(spec.latency_stress_ms),
            "note": "receipt times are observed or explicitly assumed per event; see each event's basis",
        },
        execution_settings={
            "kind": "predictive_research",
            "portfolio": "not applicable: no orders, holdings or cash are simulated",
        },
        rate_assumptions_used=["cost_bps_low: ASSUMPTION", "cost_bps_high: ASSUMPTION"],
        date_range={"start_time": spec.start, "end_time": spec.end},
        metrics=result,
        git_commit=commit,
        git_dirty=dirty,
        code_version=f"external_intelligence/{__version__}",
        warnings=warnings,
        error=error,
        traceback=trace,
        tool_versions={
            "python": sys.version.split()[0], "external_intelligence": __version__,
            "ablation": ABLATION_VERSION, "model": MODEL_VERSION, **inputs.provider_versions,
        },
    )
    run_dir = registry.write(record)
    if result is not None:
        (run_dir / "ablation_result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )
    return RecordedRun(record.run_id, run_dir, record.status, result, error)


def _features_by_experiment(inputs: AblationInputs, spec: AblationSpec) -> Mapping[str, list[str]]:
    from ..features.definitions import default_feature_definitions

    definitions = list(inputs.definitions) if inputs.definitions is not None else default_feature_definitions()
    modelled = [d for d in definitions if d.metric not in MODEL_EXCLUDED_METRICS]
    return {
        str(k): [d.name for d in modelled if d.family in EXPERIMENT_FAMILIES[k]]
        for k in sorted(set(spec.experiments)) if k in EXPERIMENT_FAMILIES
    }
