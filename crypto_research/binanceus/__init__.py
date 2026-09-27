"""Binance.US public market-data access.

This subpackage is read-only by construction:

* the transport refuses any HTTP method other than GET;
* the transport refuses any path outside PUBLIC_ENDPOINTS;
* there is no request signing, no API-key header, and no secret handling.

Binance.com is a different exchange with different pairs, different filters
and different fees. Nothing here should ever be pointed at it.
"""

from .constants import BASE_URL, PUBLIC_ENDPOINTS
from .filters import SymbolRules, SymbolRuleBook
from .public_client import BinanceUSPublicClient

__all__ = [
    "BASE_URL",
    "PUBLIC_ENDPOINTS",
    "BinanceUSPublicClient",
    "SymbolRuleBook",
    "SymbolRules",
]
