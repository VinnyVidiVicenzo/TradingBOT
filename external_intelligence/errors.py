"""Errors raised by the external-intelligence zone.

They hang off the engine's own hierarchy so the CLI's single ``except`` for
``CryptoResearchError`` covers them, and none of them has any connection to a
trading action: a provider failure is a data-quality event, never a reason to
sell, buy or resize anything.
"""

from __future__ import annotations

from crypto_research.errors import CryptoResearchError


class ExternalIntelligenceError(CryptoResearchError):
    """Base for everything this subsystem raises."""


class EventValidationError(ExternalIntelligenceError, ValueError):
    """An event violated the normalized schema."""


class ProviderError(ExternalIntelligenceError):
    """A provider could not be read (HTTP error, bad status, unusable reply)."""


class ProviderTimeout(ProviderError):
    """A provider did not answer in time."""


class ProviderRateLimited(ProviderError):
    """A provider (or our own limiter) said to slow down."""

    def __init__(self, message: str, retry_after_ms: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_ms = retry_after_ms


class ProviderSchemaError(ProviderError):
    """A payload could not be interpreted at all (not merely one bad record)."""


class ProviderNotIntegrated(ExternalIntelligenceError):
    """A documented placeholder was asked to do real work."""


class LiveIngestionNotEnabled(ExternalIntelligenceError):
    """Live ingestion for this provider is deliberately off pending approval."""


class ProviderUnhealthy(ExternalIntelligenceError):
    """A consumer asked for features from a provider that is currently unhealthy."""


class StaleDataError(ExternalIntelligenceError):
    """External data was too old to be used."""
