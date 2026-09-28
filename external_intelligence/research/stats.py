"""Small, dependency-free statistics, chosen for honesty about overlapping data.

Forward returns measured every hour over a 24-hour horizon overlap almost
completely: consecutive observations share 23 of their 24 hours. Treating them
as independent overstates the evidence roughly twenty-fold. Two tools here
address that:

* :func:`effective_samples` divides by the overlap factor, and
* :func:`block_bootstrap_mean` resamples *blocks* at least one horizon long, so
  the dependence between neighbours is kept inside a block instead of being
  ignored.

:func:`holm_adjust` corrects a family of comparisons for having looked at
several. All randomness is seeded, so a rerun gives the same answer.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any, Sequence


def summary_stats(values: Sequence[float]) -> dict[str, Any]:
    """Count, median, mean, spread and the fraction positive."""
    n = len(values)
    if n == 0:
        return {"n": 0, "median": None, "mean": None, "stdev": None, "iqr": None,
                "positive_frequency": None}
    iqr = None
    if n >= 2:
        q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
        iqr = q3 - q1
    return {
        "n": n,
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "stdev": statistics.stdev(values) if n >= 2 else None,
        "iqr": iqr,
        "positive_frequency": sum(1 for v in values if v > 0) / n,
    }


def effective_samples(n: int, overlap_factor: float) -> float:
    """``n`` divided by how many neighbouring observations share the same outcome window."""
    return n / max(1.0, overlap_factor)


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already sorted sequence, ``q`` in [0, 1]."""
    if not sorted_values:
        raise ValueError("no values")
    position = q * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return sorted_values[low]
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def holm_adjust(p_values: Sequence[float]) -> list[float]:
    """Holm step-down adjusted p-values, returned in the input order."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, min(1.0, (m - rank) * p_values[index]))
        adjusted[index] = running
    return adjusted


def block_bootstrap_mean(
    values: Sequence[float], *, block_length: int, iterations: int, seed: int
) -> dict[str, Any]:
    """Mean of ``values`` with a moving-block-bootstrap interval and one-sided p-value.

    The p-value is for the null "the true mean is zero" against "greater than
    zero": the values are re-centred on zero, resampled in overlapping blocks of
    ``block_length`` (so short-range dependence stays intact), and the p-value is
    the share of resampled means at least as large as the observed mean. With too
    few values for even a few blocks it reports ``insufficient`` instead of a number.
    """
    n = len(values)
    block = max(1, block_length)
    if n < 4 * block:
        return {"mean": statistics.fmean(values) if n else None, "ci_low": None, "ci_high": None,
                "p_one_sided": None, "insufficient": True, "n": n, "block_length": block}
    mean = statistics.fmean(values)
    prefix = [0.0]
    for v in values:
        prefix.append(prefix[-1] + v)
    centred_prefix = [p - mean * i for i, p in enumerate(prefix)]
    starts = n - block + 1
    blocks_per_draw = n // block
    draw_length = blocks_per_draw * block
    rng = random.Random(seed)
    boot: list[float] = []
    null: list[float] = []
    for _ in range(iterations):
        total = 0.0
        null_total = 0.0
        for _ in range(blocks_per_draw):
            s = rng.randrange(starts)
            total += prefix[s + block] - prefix[s]
            null_total += centred_prefix[s + block] - centred_prefix[s]
        boot.append(total / draw_length)
        null.append(null_total / draw_length)
    boot.sort()
    exceed = sum(1 for m in null if m >= mean)
    return {
        "mean": mean,
        "ci_low": percentile(boot, 0.025),
        "ci_high": percentile(boot, 0.975),
        "p_one_sided": (1 + exceed) / (iterations + 1),
        "insufficient": False,
        "n": n,
        "block_length": block,
    }
