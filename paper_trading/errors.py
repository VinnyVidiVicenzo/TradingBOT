"""Errors raised by the paper-trading rehearsal.

They hang off the engine's own hierarchy. None of them has any connection to a
trading action: this package only reads public market data and re-runs the
already-audited research backtester.
"""

from __future__ import annotations

from crypto_research.errors import CryptoResearchError


class PaperTradingError(CryptoResearchError):
    """Base for everything this package raises."""


class PlanMismatchError(PaperTradingError):
    """The rehearsal and benchmark configs disagree on the data they must share."""


class FeedUnavailable(PaperTradingError):
    """Fresh market data could not be fetched. No run was attempted this tick."""
