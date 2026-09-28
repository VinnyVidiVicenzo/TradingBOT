"""Chronological evaluation splits. No shuffling, and no overlap leakage.

Time-series samples are never shuffled: a model must be fitted on the past and
scored on the future. Two layouts are supported:

* **holdout** -- train, then validation, then an untouched test period. The
  validation fold is for looking at; the test fold is scored once, on a model that
  has never seen it.
* **walk-forward** -- an expanding training window and consecutive test blocks,
  refitting before each block.

*Purging.* A sample taken at time ``t`` with a forward-return horizon ``h`` has an
outcome window ``[t, t + h]``. A training sample whose outcome window reaches into
the test period would leak the test period's prices into training, so it is
dropped: only samples with ``t + h <= test_start`` may train.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True, slots=True)
class HoldoutSpec:
    train_end: int  # training samples end here; validation begins
    validation_end: int  # validation is [train_end, validation_end); the test follows


@dataclass(frozen=True, slots=True)
class WalkForwardSpec:
    initial_train_ms: int
    test_block_ms: int


@dataclass(frozen=True, slots=True)
class Fold:
    name: str
    kind: str  # "validation" | "test" | "walk_forward"
    train: tuple[int, ...]  # indices into the sample list
    test: tuple[int, ...]


def _check_sorted(times: Sequence[int]) -> None:
    if any(later < earlier for earlier, later in zip(times, times[1:])):
        raise ValueError("sample times must be in ascending order; they are never shuffled")


def _purged_train(times: Sequence[int], horizon_ms: int, test_start: int) -> tuple[int, ...]:
    return tuple(i for i, t in enumerate(times) if t + horizon_ms <= test_start)


def holdout_folds(times: Sequence[int], horizon_ms: int, spec: HoldoutSpec) -> list[Fold]:
    _check_sorted(times)
    if not spec.train_end < spec.validation_end:
        raise ValueError("train_end must come before validation_end")
    validation = tuple(i for i, t in enumerate(times) if spec.train_end <= t < spec.validation_end)
    test = tuple(i for i, t in enumerate(times) if t >= spec.validation_end)
    return [
        Fold("validation", "validation", _purged_train(times, horizon_ms, spec.train_end), validation),
        Fold("test", "test", _purged_train(times, horizon_ms, spec.validation_end), test),
    ]


def walk_forward_folds(times: Sequence[int], horizon_ms: int, spec: WalkForwardSpec) -> list[Fold]:
    _check_sorted(times)
    if not times:
        return []
    folds: list[Fold] = []
    block_start = times[0] + spec.initial_train_ms
    number = 1
    while block_start <= times[-1]:
        block_end = block_start + spec.test_block_ms
        test = tuple(i for i, t in enumerate(times) if block_start <= t < block_end)
        train = _purged_train(times, horizon_ms, block_start)
        if test and train:
            folds.append(Fold(f"wf_{number:02d}", "walk_forward", train, test))
            number += 1
        block_start = block_end
    return folds


def assert_no_leakage(times: Sequence[int], horizon_ms: int, fold: Fold) -> None:
    """Raise if any training outcome window reaches into the fold's test period."""
    if not fold.train or not fold.test:
        return
    test_start = min(times[i] for i in fold.test)
    latest_train_outcome = max(times[i] + horizon_ms for i in fold.train)
    if latest_train_outcome > test_start:
        raise AssertionError(
            f"fold {fold.name}: a training outcome window ends at {latest_train_outcome}, "
            f"after the test period begins at {test_start}"
        )
