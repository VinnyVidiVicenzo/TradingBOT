"""Point-in-time features built from normalized external events.

Features describe. They never recommend: nothing in this package maps a feature to
an action, and whether a feature predicts anything is decided in ``research``.
"""

from .definitions import (
    FeatureDefinition,
    Metric,
    default_feature_definitions,
    derivatives_definitions,
    executed_definitions,
    liquidation_definitions,
    stablecoin_definitions,
    transfer_definitions,
)
from .pipeline import (
    DEFAULT_PROVIDERS,
    FeaturePipeline,
    FeatureSnapshot,
    FeatureStatus,
    FeatureValue,
    InMemoryEventSource,
    RepositoryEventSource,
)
from .windows import format_window, parse_window

__all__ = [
    "DEFAULT_PROVIDERS",
    "FeatureDefinition",
    "FeaturePipeline",
    "FeatureSnapshot",
    "FeatureStatus",
    "FeatureValue",
    "InMemoryEventSource",
    "Metric",
    "RepositoryEventSource",
    "default_feature_definitions",
    "derivatives_definitions",
    "executed_definitions",
    "format_window",
    "liquidation_definitions",
    "parse_window",
    "stablecoin_definitions",
    "transfer_definitions",
]
