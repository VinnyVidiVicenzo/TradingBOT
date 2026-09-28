"""Derived event categories: an "extreme" reading of a feature, judged only against its own past.

Some studies are about a *raw* event (a large exchange inflow, a stablecoin mint).
Others are about a moment when a feature was unusual: an extreme executed-buy
imbalance, a liquidation spike, an unusual jump in open interest. Those have no
provider record to hang a study on, so they are defined here.

A trigger fires at time ``t`` when the feature's value at ``t`` lies beyond a
quantile of the same feature's values *strictly before* ``t`` (within a lookback).
Nothing at or after ``t`` enters the threshold, and the value itself was computed
point-in-time, so ``t`` is the moment the reading was actionable: it is the entry
time for the study. Too little history means no trigger, not a guess.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Sequence

from ..features.normalization import Observation
from .stats import percentile

DEFAULT_MIN_OBSERVATIONS = 30


@dataclass(frozen=True, slots=True)
class Trigger:
    trigger_id: str
    entry_time: int
    label: str
    value: float
    threshold: float


def exceedance_triggers(
    label: str,
    series: Sequence[Observation],
    *,
    lookback_ms: int,
    quantile: float,
    direction: str = "above",
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> list[Trigger]:
    if not 0.0 < quantile < 1.0:
        raise ValueError("quantile must be strictly between 0 and 1")
    if direction not in ("above", "below"):
        raise ValueError("direction must be 'above' or 'below'")
    if lookback_ms <= 0 or min_observations < 2:
        raise ValueError("lookback must be positive and at least two observations are needed")
    ordered = sorted(series, key=lambda o: o.time)
    times = [o.time for o in ordered]
    values = [o.value for o in ordered]
    out: list[Trigger] = []
    for index, observation in enumerate(ordered):
        if observation.value is None:
            continue
        low = bisect.bisect_left(times, observation.time - lookback_ms)
        high = bisect.bisect_left(times, observation.time)  # strictly earlier
        history = sorted(v for v in values[low:high] if v is not None)
        if len(history) < min_observations:
            continue
        threshold = percentile(history, quantile)
        hit = observation.value > threshold if direction == "above" else observation.value < threshold
        if hit:
            out.append(Trigger(f"{label}@{observation.time}", observation.time, label, observation.value, threshold))
    return out
