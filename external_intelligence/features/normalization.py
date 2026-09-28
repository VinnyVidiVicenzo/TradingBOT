"""Lagged-only normalisation.

A raw dollar figure is hard to compare across years: what is unusual now was
ordinary in a busier market. Normalising a value against its own recent past
helps, provided the past is genuinely the past.

The strict rule: at time ``T`` a statistic may use only observations made
*strictly before* ``T`` (and within the lookback), and each of those observations
was itself computed point-in-time from what was available when it was made. The
value being normalised is compared *with* that history, never included in it. Any
observation at or after ``T`` is ignored, so changing the future cannot change a
normalised value; a test checks exactly that.

Too little history returns ``None`` rather than a number built on a handful of
points. A z-score on a series with no variation is undefined, not zero.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from .pipeline import FeaturePipeline, FeatureStatus, FeatureValue
from .windows import parse_window

DEFAULT_MIN_OBSERVATIONS = 10


@dataclass(frozen=True, slots=True)
class Observation:
    """A feature value as it was computed at ``time``. ``None`` means missing."""

    time: int
    value: float | None


def trailing_values(observations: Sequence[Observation], as_of: int, lookback_ms: int) -> list[float]:
    """Non-missing values from ``[as_of - lookback, as_of)``. Nothing at or after ``as_of``."""
    low = as_of - lookback_ms
    return [o.value for o in observations if o.value is not None and low <= o.time < as_of]


def rolling_zscore(
    observations: Sequence[Observation], as_of: int, current: float | None, *,
    lookback_ms: int, min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> float | None:
    values = trailing_values(observations, as_of, lookback_ms)
    if current is None or len(values) < max(2, min_observations):
        return None
    spread = statistics.stdev(values)
    if spread == 0 or not math.isfinite(spread):
        return None
    return (current - statistics.fmean(values)) / spread


def rolling_percentile(
    observations: Sequence[Observation], as_of: int, current: float | None, *,
    lookback_ms: int, min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> float | None:
    """Where ``current`` ranks among the trailing values, 0 to 1 (ties count half)."""
    values = trailing_values(observations, as_of, lookback_ms)
    if current is None or len(values) < min_observations:
        return None
    below = sum(1 for v in values if v < current)
    equal = sum(1 for v in values if v == current)
    return (below + 0.5 * equal) / len(values)


def ratio_to_trailing_median(
    observations: Sequence[Observation], as_of: int, current: float | None, *,
    lookback_ms: int, min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> float | None:
    values = trailing_values(observations, as_of, lookback_ms)
    if current is None or len(values) < min_observations:
        return None
    median = statistics.median(values)
    return None if median == 0 else current / median


def ratio_to_trailing_mean(
    observations: Sequence[Observation], as_of: int, current: float | None, *,
    lookback_ms: int, min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> float | None:
    values = trailing_values(observations, as_of, lookback_ms)
    if current is None or len(values) < min_observations:
        return None
    mean = statistics.fmean(values)
    return None if mean == 0 else current / mean


def share_of_trailing_total(
    total_observations: Sequence[Observation], as_of: int, current: float | None, *,
    lookback_ms: int, min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> float | None:
    """``current`` as a multiple of the typical total flow over the trailing period.

    For example an hour's exchange inflow against the trailing average of total
    (inflow plus outflow) exchange flow per hour.
    """
    return ratio_to_trailing_mean(
        total_observations, as_of, current, lookback_ms=lookback_ms, min_observations=min_observations
    )


def asset_relative(value: float | None, reference: float | None) -> float | None:
    """``value`` divided by a *lagged* reference (for example trailing dollar volume).

    The caller must supply a reference that was itself computed before ``as_of``.
    """
    if value is None or reference is None or reference <= 0:
        return None
    return value / reference


# --------------------------------------------------------------------------
# named normalised features, computed from the pipeline's own point-in-time values
# --------------------------------------------------------------------------


class Method(str, Enum):
    ZSCORE = "zscore"
    PERCENTILE = "percentile"
    RATIO_TO_MEDIAN = "ratio_to_median"
    RATIO_TO_MEAN = "ratio_to_mean"


_METHODS = {
    Method.ZSCORE: rolling_zscore,
    Method.PERCENTILE: rolling_percentile,
    Method.RATIO_TO_MEDIAN: ratio_to_trailing_median,
    Method.RATIO_TO_MEAN: ratio_to_trailing_mean,
}


@dataclass(frozen=True, slots=True)
class NormalizedFeatureSpec:
    name: str
    base_feature: str
    method: Method
    lookback_ms: int
    min_observations: int = DEFAULT_MIN_OBSERVATIONS


def default_normalized_specs() -> list[NormalizedFeatureSpec]:
    thirty_days = parse_window("30d")
    return [
        NormalizedFeatureSpec("exchange_inflow_zscore_30d", "exchange_inflow_usd_1h", Method.ZSCORE, thirty_days),
        NormalizedFeatureSpec("whale_trade_flow_percentile_30d", "whale_net_trade_flow_1h", Method.PERCENTILE, thirty_days),
        NormalizedFeatureSpec("transfer_value_vs_30d_median", "whale_transfer_usd_1h", Method.RATIO_TO_MEDIAN, thirty_days),
    ]


def build_series(
    pipeline: FeaturePipeline, asset: str, feature_name: str, times: Sequence[int]
) -> list[Observation]:
    """The feature as it would have been computed at each of ``times``, point-in-time."""
    series: list[Observation] = []
    for moment in times:
        value = pipeline.compute(asset, moment, names=[feature_name]).get(feature_name).as_float()
        series.append(Observation(moment, value))
    return series


class NormalizedFeatureBuilder:
    """Computes normalised features at a decision time from strictly earlier history."""

    def __init__(
        self, pipeline: FeaturePipeline, specs: Sequence[NormalizedFeatureSpec] | None = None,
        *, step_ms: int,
    ) -> None:
        if step_ms <= 0:
            raise ValueError("step_ms must be positive")
        self._pipeline = pipeline
        self.specs = list(specs if specs is not None else default_normalized_specs())
        self.step_ms = step_ms

    def compute(self, asset: str, decision_time: int) -> Mapping[str, FeatureValue]:
        history_cache: dict[tuple[str, int], list[Observation]] = {}
        out: dict[str, FeatureValue] = {}
        for spec in self.specs:
            now_value = self._pipeline.compute(asset, decision_time, names=[spec.base_feature]).get(spec.base_feature)
            if now_value.is_missing:
                out[spec.name] = FeatureValue(
                    None, now_value.status, f"base feature {spec.base_feature} is missing: {now_value.detail}"
                )
                continue
            key = (spec.base_feature, spec.lookback_ms)
            if key not in history_cache:
                steps = spec.lookback_ms // self.step_ms
                grid = [decision_time - k * self.step_ms for k in range(steps, 0, -1)]
                history_cache[key] = build_series(self._pipeline, asset, spec.base_feature, grid)
            result = _METHODS[spec.method](
                history_cache[key], decision_time, now_value.as_float(),
                lookback_ms=spec.lookback_ms, min_observations=spec.min_observations,
            )
            if result is None:
                out[spec.name] = FeatureValue(
                    None, FeatureStatus.MISSING_NO_HISTORY,
                    "too little (or constant) prior history for a normalised value",
                )
            else:
                out[spec.name] = FeatureValue(result, FeatureStatus.OK, "", now_value.n_events)  # type: ignore[arg-type]
        return out
