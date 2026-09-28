"""The hypothesis registry: every feature family needs a testable claim.

These are hypotheses, not assumptions. Each is stated so that it could be wrong,
comes with a mechanism that can be said in a sentence *without reference to any
backtest*, names the features it concerns, and lists the controls any test must
include. Status stays ``UNTESTED`` in code: results live in the experiment
registry as append-only runs, and a status is never edited to match a result.

The controls are the same for every hypothesis: price-derived features (returns,
volatility, trend, volume, a spread/range proxy). The question is always whether
the external variable adds anything *beyond* what those already show.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

from crypto_research.experiments.registry import ExperimentRegistry

from .baseline import BASELINE_FEATURES


@dataclass(frozen=True, slots=True)
class Hypothesis:
    id: str
    title: str
    statement: str
    mechanism: str
    feature_names: tuple[str, ...]
    experiments: tuple[int, ...]
    caveat: str
    required_controls: tuple[str, ...] = BASELINE_FEATURES
    status: str = "UNTESTED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "statement": self.statement,
            "mechanism": self.mechanism, "feature_names": list(self.feature_names),
            "experiments": list(self.experiments), "caveat": self.caveat,
            "required_controls": list(self.required_controls), "status": self.status,
        }


HYPOTHESES: dict[str, Hypothesis] = {
    "A": Hypothesis(
        id="A",
        title="Large exchange inflows and weaker forward returns",
        statement=(
            "Unusually large exchange inflows, after controlling for recent price, volume "
            "and volatility, may predict weaker forward returns."
        ),
        mechanism=(
            "Value moved to an exchange is closer to being sold, so a burst of inflow may be "
            "sell-side supply arriving; the same transfers can equally be custody or internal "
            "moves, in which case there is no effect."
        ),
        feature_names=(
            "exchange_inflow_usd_1h", "exchange_inflow_usd_4h", "exchange_net_flow_usd_1h",
            "exchange_net_flow_usd_4h", "exchange_inflow_zscore_30d",
        ),
        experiments=(1, 3),
        caveat="Exchange attribution comes from the provider's own labels and can be wrong or change.",
    ),
    "B": Hypothesis(
        id="B",
        title="Large exchange outflows and reduced sell-side supply",
        statement=(
            "Large exchange outflows may sometimes indicate reduced immediately available "
            "sell-side supply, but the effect may be weak or regime-dependent."
        ),
        mechanism=(
            "Coins withdrawn from an exchange are further from being sold; but withdrawals are "
            "often custody changes, and any effect may appear only in some market regimes."
        ),
        feature_names=(
            "exchange_outflow_usd_1h", "exchange_outflow_usd_4h", "exchange_net_flow_usd_1h",
            "exchange_net_flow_usd_4h",
        ),
        experiments=(1, 3),
        caveat="Test by regime (trend and volatility state) before believing any pooled result.",
    ),
    "C": Hypothesis(
        id="C",
        title="Executed whale trades carry more direction than wallet transfers",
        statement=(
            "Executed whale net buying may contain more directional information than wallet "
            "transfers because it reflects actual market execution."
        ),
        mechanism=(
            "An executed trade is an action taken at a price, while a transfer is only a "
            "movement of coins; trades can therefore say more about intent -- unless the "
            "'whale' flow is one large account splitting orders, which adds noise, not signal."
        ),
        feature_names=(
            "whale_buy_usd_1h", "whale_sell_usd_1h", "whale_net_trade_flow_1h",
            "whale_buy_sell_ratio_1h", "large_trade_count_1h", "whale_trade_flow_percentile_30d",
        ),
        experiments=(2, 3),
        caveat=(
            "Compare Experiment 2 with Experiment 1, not only with the baseline. The venue "
            "behind an executed-trade feed is not Binance.US."
        ),
    ),
    "D": Hypothesis(
        id="D",
        title="Liquidation extremes and short-horizon reversal",
        statement="Extreme liquidation events may create short-horizon dislocations or mean reversion.",
        mechanism=(
            "Forced closures are not chosen trades; they can push price past where voluntary "
            "trading would settle it, and it may come back once the forced flow ends."
        ),
        feature_names=(
            "long_liquidations_usd_1h", "short_liquidations_usd_1h", "liquidation_imbalance_1h",
        ),
        experiments=(2, 3),
        caveat="Liquidations happen on derivatives venues; this project trades spot only.",
    ),
    "E": Hypothesis(
        id="E",
        title="Funding and open interest as crowding, direction unknown",
        statement=(
            "Funding and open-interest combinations may identify crowded positioning, but "
            "direction cannot be inferred from either variable independently."
        ),
        mechanism=(
            "When many traders hold the same side with borrowed money an adverse move forces "
            "them out; which side is crowded depends on funding and open interest read "
            "together, never on either alone."
        ),
        feature_names=("funding_rate", "funding_rate_change", "open_interest", "open_interest_change"),
        experiments=(2, 3),
        caveat="Only interaction terms can express the hypothesis; single variables cannot.",
    ),
}


def all_hypotheses() -> list[Hypothesis]:
    return [HYPOTHESES[key] for key in sorted(HYPOTHESES)]


def runs_for_hypothesis(registry: ExperimentRegistry, hypothesis_id: str) -> list[dict[str, Any]]:
    """Every recorded run that names ``hypothesis_id`` in its configuration."""
    return [
        record for record in _iter_records(registry)
        if hypothesis_id in (record.get("config", {}).get("hypotheses") or [])
    ]


def count_prior_runs(registry: ExperimentRegistry, *, experiment_name: str) -> int:
    """How many runs already exist under ``experiment_name`` -- "runs so far on this question"."""
    return sum(1 for entry in registry.list_runs() if entry.get("experiment_name") == experiment_name)


def _iter_records(registry: ExperimentRegistry) -> Iterator[dict[str, Any]]:
    yield from registry.iter_runs()
