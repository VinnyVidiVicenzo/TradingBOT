"""The four providers considered for later research. None is integrated."""

from __future__ import annotations

from .base import NotIntegratedProvider, ProviderSpec

ARKHAM_SPEC = ProviderSpec(
    name="arkham",
    display_name="Arkham",
    intended_role=(
        "wallet and entity attribution",
        "labelling of exchanges, custodians, funds and market makers",
    ),
    candidate_features=(
        "share of a transfer's value moving between labelled and unlabelled addresses",
        "net flow of labelled funds or market makers versus exchanges",
        "counts of transfers by attributed entity-type pair",
    ),
    update_frequency="near real time for transfers; labels are revised continuously",
    historical_data_requirements=(
        "the label an address had AT THE TIME of each transfer (point-in-time label "
        "history). Today's labels applied to old transfers are look-ahead."
    ),
    likely_cost_category="paid or application-based API access (verify current terms)",
    attribution_uncertainty=(
        "high: labels are the provider's inference, can be wrong, and change; a "
        "confidence figure is recorded only if the provider supplies one"
    ),
    justifying_hypothesis=(
        "Attribution improves the exchange-flow features: flows between attributed "
        "entity types carry information that raw transfers do not (Experiment 4 versus 1)."
    ),
    experiment="Experiment 4 (entity attribution)",
)

NANSEN_SPEC = ProviderSpec(
    name="nansen",
    display_name="Nansen",
    intended_role=(
        "labelled smart-money wallet cohorts",
        "cohort accumulation and distribution",
    ),
    candidate_features=(
        "cohort net change in holdings of an asset",
        "number of cohort wallets adding versus reducing",
        "concentration of cohort holdings",
    ),
    update_frequency="hourly to daily",
    historical_data_requirements=(
        "point-in-time cohort membership. A cohort defined by past performance is "
        "selected with hindsight, so membership must be as of each test date."
    ),
    likely_cost_category="paid subscription, with API access on higher tiers (verify)",
    attribution_uncertainty=(
        "high and biased: 'smart money' is defined by earlier success, which builds "
        "selection bias into the cohort itself"
    ),
    justifying_hypothesis=(
        "Cohort net accumulation predicts forward returns after controlling for "
        "price and volume, using cohort membership frozen before the test window."
    ),
    experiment="Experiment 4 (entity attribution)",
)

CRYPTOQUANT_SPEC = ProviderSpec(
    name="cryptoquant",
    display_name="CryptoQuant",
    intended_role=(
        "exchange flows and exchange reserves",
        "whale ratio and aggregate holder behaviour",
    ),
    candidate_features=(
        "change in exchange reserves",
        "exchange net flow as the provider aggregates it",
        "whale ratio (share of exchange inflow from the largest transfers)",
        "stablecoin reserves on exchanges",
    ),
    update_frequency="hourly and daily aggregates",
    historical_data_requirements=(
        "vintage data: aggregates are restated when exchange labels change, so a "
        "backtest needs the value as first published, not the revised history"
    ),
    likely_cost_category="free web tier plus paid API plans (verify)",
    attribution_uncertainty=(
        "medium to high: aggregates depend on the provider's exchange-address labels, "
        "which are revised retroactively"
    ),
    justifying_hypothesis=(
        "Exchange reserve and net-flow aggregates add predictive information beyond "
        "price and volume at 1 to 7 day horizons (Experiment 5)."
    ),
    experiment="Experiment 5 (aggregate on-chain regime)",
)

GLASSNODE_SPEC = ProviderSpec(
    name="glassnode",
    display_name="Glassnode",
    intended_role=(
        "holder cohorts and network activity",
        "realized-value metrics and exchange balances",
        "slow, long-term on-chain regime features",
    ),
    candidate_features=(
        "MVRV and realized-value ratios (an MVRV series is already used from Coin Metrics)",
        "supply in profit and holder-cohort age bands",
        "active addresses and exchange balances",
    ),
    update_frequency="daily, with finer resolution on paid plans",
    historical_data_requirements=(
        "long daily history with vintage tracking; metrics can be recomputed as "
        "entity clustering improves"
    ),
    likely_cost_category="restricted free tier plus paid plans; API on paid plans (verify)",
    attribution_uncertainty=(
        "medium: metrics rest on address-clustering heuristics that are refined over time"
    ),
    justifying_hypothesis=(
        "Slow on-chain regime features improve long-horizon risk estimates, not "
        "short-horizon timing (Experiment 5)."
    ),
    experiment="Experiment 5 (aggregate on-chain regime)",
)


class ArkhamPlaceholder(NotIntegratedProvider):
    name = "arkham"
    spec = ARKHAM_SPEC


class NansenPlaceholder(NotIntegratedProvider):
    name = "nansen"
    spec = NANSEN_SPEC


class CryptoQuantPlaceholder(NotIntegratedProvider):
    name = "cryptoquant"
    spec = CRYPTOQUANT_SPEC


class GlassnodePlaceholder(NotIntegratedProvider):
    name = "glassnode"
    spec = GLASSNODE_SPEC


ALL_PLACEHOLDERS = (ArkhamPlaceholder, NansenPlaceholder, CryptoQuantPlaceholder, GlassnodePlaceholder)
ALL_SPECS = (ARKHAM_SPEC, NANSEN_SPEC, CRYPTOQUANT_SPEC, GLASSNODE_SPEC)
