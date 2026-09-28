"""When is "no events" a zero, and when is it "we do not know"?

A feature that says "$0 flowed into exchanges in the last hour" is only true if
the provider was actually being read for that whole hour. If the ingestion was
down, or the imported file only covered part of the window, the honest value is
*missing*, not zero. Coverage intervals record the periods during which a
provider feed is known to have been complete, per feature family.

Coverage is declared, never inferred. The first and last event in a file prove
nothing about what lies between them, so an import without a declared window
records no coverage, and every feature that needs it stays missing.

Coverage also has to be *usable*, not merely declared. A window is only a true
zero to a run that is allowed to see the events that would have filled it:

* an interval declared on **estimated** receipt times only counts when the run
  allows estimates (otherwise every event in it is invisible, and calling the
  window empty would be a silent zero);
* an interval declared on a file imported "just now" (``known_from`` = the moment
  of import) only counts for decisions made at or after that moment, because
  before it we had not received anything;
* an interval whose events carry **no** receipt time never counts;
* an interval declared for **some assets only** (an export filtered to BTC) says
  nothing about the others, and never covers a market-wide feature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

# Feature families. A provider covers a family, not "everything".
FAMILY_TRANSFERS = "transfers"
FAMILY_EXECUTED = "executed_trades"
FAMILY_LIQUIDATIONS = "liquidations"
FAMILY_DERIVATIVES = "derivatives"
ALL_FAMILIES = (FAMILY_TRANSFERS, FAMILY_EXECUTED, FAMILY_LIQUIDATIONS, FAMILY_DERIVATIVES)

# What the events inside an interval say about when we received them.
RECEIPTS_OBSERVED = "observed"
RECEIPTS_ESTIMATED = "estimated"
RECEIPTS_NONE = "none"
_RECEIPT_KINDS = (RECEIPTS_OBSERVED, RECEIPTS_ESTIMATED, RECEIPTS_NONE)


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    """A period (inclusive, epoch ms) during which one family was fully observed."""

    provider: str
    family: str
    covered_from: int
    covered_to: int
    basis: str  # "declared_import_window" | "live_ingestion" (+ "_losses_accepted")
    source_ref: str | None = None
    receipts: str = RECEIPTS_OBSERVED
    # A decision made before this moment cannot rely on the interval (we had not yet received it).
    known_from: int | None = None
    # ``None`` means every asset the provider reports; otherwise only these (upper-case).
    assets: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.assets is not None:
            cleaned = tuple(sorted({a.strip().upper() for a in self.assets if a and a.strip()}))
            if not cleaned:
                raise ValueError("an asset-scoped coverage interval must name at least one asset")
            object.__setattr__(self, "assets", cleaned)
        if self.covered_to < self.covered_from:
            raise ValueError("coverage interval ends before it starts")
        if self.receipts not in _RECEIPT_KINDS:
            raise ValueError(f"receipts must be one of {_RECEIPT_KINDS}")


class CoverageIndex:
    """Declared intervals, queryable for "was [start, end] fully covered, and may this run rely on it?"."""

    def __init__(self, intervals: Iterable[CoverageInterval] = ()) -> None:
        self._by_key: dict[tuple[str, str], list[CoverageInterval]] = {}
        for interval in intervals:
            self.add(interval)

    def add(self, interval: CoverageInterval) -> None:
        self._by_key.setdefault((interval.provider, interval.family), []).append(interval)

    def covers(
        self,
        provider: str,
        family: str,
        start: int,
        end: int,
        *,
        allow_estimated: bool = False,
        as_of: int | None = None,
        asset: str | None = None,
    ) -> bool:
        """Is ``[start, end]`` covered by intervals this run may rely on?

        ``allow_estimated`` admits intervals declared on estimated receipt times.
        ``asset`` is the asset the feature is about; ``None`` means the feature spans
        all assets (issuance across every stablecoin), which only an all-assets
        interval can cover.
        ``as_of`` is the moment the decision is made: an interval only known from
        a later moment is not usable. Adjacent intervals (1 ms apart) count as
        continuous; a gap does not.
        """
        usable = sorted(
            (i.covered_from, i.covered_to)
            for i in self._by_key.get((provider, family), ())
            if self._eligible(i, allow_estimated, as_of, asset)
        )
        merged: list[tuple[int, int]] = []
        for lo, hi in usable:
            if merged and lo <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        return any(lo <= start and end <= hi for lo, hi in merged)

    def intervals(self, provider: str, family: str) -> Sequence[CoverageInterval]:
        return tuple(self._by_key.get((provider, family), ()))

    @staticmethod
    def _eligible(
        interval: CoverageInterval, allow_estimated: bool, as_of: int | None, asset: str | None
    ) -> bool:
        if interval.receipts == RECEIPTS_NONE:
            return False
        if interval.receipts == RECEIPTS_ESTIMATED and not allow_estimated:
            return False
        if interval.known_from is not None and as_of is not None and as_of < interval.known_from:
            return False
        if interval.assets is not None and (asset is None or asset.strip().upper() not in interval.assets):
            return False
        return True
