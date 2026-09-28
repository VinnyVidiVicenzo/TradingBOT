"""Market-data-only features: the baseline every external feature must beat.

The question is never "did price move after a whale event?" but "did the external
variable improve a forecast *after* accounting for what price, volume and
volatility already show?" This is the "what price already shows" half. It uses
only completed, observable bars, and is deliberately ordinary.

All values are per *bar* of the interval supplied (hourly bars give hourly
returns). Log returns are used for their additivity. Anything without enough
history is ``None``.
"""

from __future__ import annotations

import bisect
import math
import statistics
from typing import Mapping, Sequence

from crypto_research.models import MarketBar

BASELINE_FEATURES = (
    "ret_1", "ret_4", "ret_24", "vol_24", "trend_24_96", "volume_ratio_24", "range_1",
)


class BaselineFeatureBuilder:
    def __init__(self, bars: Sequence[MarketBar], *, extra_delay_ms: int = 0) -> None:
        if not bars:
            raise ValueError("no bars")
        ordered = sorted(bars, key=lambda b: b.open_time)
        self._open_time = [b.open_time for b in ordered]
        self._resolution = ordered[0].close_time - ordered[0].open_time + 1
        self._observable = [b.observable_at + extra_delay_ms for b in ordered]
        self._close = [float(b.close) for b in ordered]
        self._high = [float(b.high) for b in ordered]
        self._low = [float(b.low) for b in ordered]
        self._volume = [
            float(b.quote_volume) if b.quote_volume is not None else float(b.volume) for b in ordered
        ]

    def _consecutive(self, first: int, last: int) -> bool:
        """True when bars ``first`` to ``last`` have no missing bar between them."""
        return self._open_time[last] - self._open_time[first] == (last - first) * self._resolution

    def features_at(self, decision_time: int) -> Mapping[str, float | None]:
        """Features from bars observable at or before ``decision_time``.

        A feature that would span a gap in the bars (an exchange outage, a hole in
        the download) is ``None``: "24 bars back" must mean 24 intervals back.
        """
        last = bisect.bisect_right(self._observable, decision_time) - 1
        out: dict[str, float | None] = {name: None for name in BASELINE_FEATURES}
        if last < 1:
            return out
        for name, bars_back in (("ret_1", 1), ("ret_4", 4), ("ret_24", 24)):
            if last - bars_back >= 0 and self._consecutive(last - bars_back, last):
                out[name] = math.log(self._close[last] / self._close[last - bars_back])
        if last >= 24 and self._consecutive(last - 24, last):
            returns = [
                math.log(self._close[i] / self._close[i - 1]) for i in range(last - 23, last + 1)
            ]
            out["vol_24"] = statistics.stdev(returns)
        if last >= 95 and self._consecutive(last - 95, last):
            short = statistics.fmean(self._close[last - 23:last + 1])
            long_ = statistics.fmean(self._close[last - 95:last + 1])
            out["trend_24_96"] = short / long_ - 1
        if last >= 23 and self._consecutive(last - 23, last) and self._volume[last] > 0:
            mean_volume = statistics.fmean(self._volume[last - 23:last + 1])
            if mean_volume > 0:
                out["volume_ratio_24"] = math.log(self._volume[last] / mean_volume)
        if self._close[last] > 0:
            out["range_1"] = (self._high[last] - self._low[last]) / self._close[last]
        return out
