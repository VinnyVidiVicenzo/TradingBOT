"""Ridge regression in plain Python, deterministic.

Kept deliberately simple: a linear model with an L2 penalty on standardised
features. The point of the ablation is to ask whether *extra information* helps a
plain forecaster, not to find the cleverest model, so the model is fixed, small,
and has one pre-declared hyper-parameter (``alpha``). Nothing here is tuned on the
test period.

Standardisation is fitted on the training rows only and reused unchanged on the
test rows, so no statistic of the test period leaks in.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

MODEL_VERSION = "ridge-1"


class RidgeModel:
    def __init__(self, alpha: float) -> None:
        if alpha <= 0:
            raise ValueError("alpha must be positive")
        self.alpha = alpha
        self._means: list[float] = []
        self._scales: list[float] = []
        self._weights: list[float] = []
        self._intercept = 0.0
        self.n_train = 0

    def fit(self, rows: Sequence[Sequence[float]], y: Sequence[float]) -> "RidgeModel":
        n = len(rows)
        if n == 0 or n != len(y):
            raise ValueError("need matching, non-empty rows and targets")
        k = len(rows[0])
        self._means = [statistics.fmean(row[j] for row in rows) for j in range(k)]
        scales = [statistics.pstdev([row[j] for row in rows]) for j in range(k)]
        self._scales = [s if s > 1e-12 else 1.0 for s in scales]  # a constant column contributes nothing
        z = [[(row[j] - self._means[j]) / self._scales[j] for j in range(k)] for row in rows]
        self._intercept = statistics.fmean(y)
        centred = [v - self._intercept for v in y]
        a = [[0.0] * k for _ in range(k)]
        b = [0.0] * k
        for zi, yi in zip(z, centred):
            for p in range(k):
                zp = zi[p]
                b[p] += zp * yi
                row_a = a[p]
                for q in range(p, k):
                    row_a[q] += zp * zi[q]
        for p in range(k):
            for q in range(p):
                a[p][q] = a[q][p]
            a[p][p] += self.alpha
        self._weights = _solve(a, b)
        self.n_train = n
        return self

    def predict(self, rows: Sequence[Sequence[float]]) -> list[float]:
        out = []
        for row in rows:
            total = self._intercept
            for j, weight in enumerate(self._weights):
                total += weight * (row[j] - self._means[j]) / self._scales[j]
            out.append(total)
        return out

    def describe(self) -> dict[str, Any]:
        return {"model": MODEL_VERSION, "alpha": self.alpha, "n_train": self.n_train}


class PrevailingMean:
    """The benchmark forecaster: the mean return seen in training, whatever the features say."""

    def __init__(self) -> None:
        self._mean = 0.0

    def fit(self, y: Sequence[float]) -> "PrevailingMean":
        self._mean = statistics.fmean(y)
        return self

    def predict(self, count: int) -> list[float]:
        return [self._mean] * count


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("the normal equations are singular")
        m[col], m[pivot] = m[pivot], m[col]
        divisor = m[col][col]
        m[col] = [v / divisor for v in m[col]]
        for r in range(n):
            if r != col and m[r][col] != 0.0:
                factor = m[r][col]
                m[r] = [rv - factor * cv for rv, cv in zip(m[r], m[col])]
    return [m[i][n] for i in range(n)]


def mse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    return math.fsum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual)
