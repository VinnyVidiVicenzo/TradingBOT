"""Documented placeholders for providers that are NOT integrated.

A placeholder exists so the research plan is written down before any money or
effort is spent: what the provider would be for, which features it would feed,
what history is needed to test it honestly, roughly what kind of cost it
carries, how uncertain its attribution is, and the hypothesis that would justify
integrating it at all.

Nothing here purchases, scrapes or calls anything. Asked to do real work, a
placeholder raises ``ProviderNotIntegrated``. Cost categories are deliberately
categories, not prices: check current pricing before any approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..base.provider import NormalizeContext, NormalizedRecord, Provider
from ..errors import ProviderNotIntegrated


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    name: str
    display_name: str
    intended_role: tuple[str, ...]
    candidate_features: tuple[str, ...]
    update_frequency: str
    historical_data_requirements: str
    likely_cost_category: str
    attribution_uncertainty: str
    justifying_hypothesis: str
    experiment: str  # which ablation experiment it would extend
    status: str = "placeholder: not integrated; nothing purchased, scraped or called"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "display_name": self.display_name,
            "intended_role": list(self.intended_role),
            "candidate_features": list(self.candidate_features),
            "update_frequency": self.update_frequency,
            "historical_data_requirements": self.historical_data_requirements,
            "likely_cost_category": self.likely_cost_category,
            "attribution_uncertainty": self.attribution_uncertainty,
            "justifying_hypothesis": self.justifying_hypothesis,
            "experiment": self.experiment,
            "status": self.status,
        }


class NotIntegratedProvider(Provider):
    """A provider that is only a written plan."""

    spec: ProviderSpec
    live_ingestion_status = "not integrated: documented placeholder"

    def normalize_record(self, record: Any, ctx: NormalizeContext) -> NormalizedRecord:
        raise ProviderNotIntegrated(f"{self.name} is a documented placeholder, not an integration")

    def describe(self) -> dict[str, Any]:
        return {**super().describe(), **self.spec.to_dict()}
