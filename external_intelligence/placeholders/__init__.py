"""Documented, non-integrated providers (Arkham, Nansen, CryptoQuant, Glassnode)."""

from .base import NotIntegratedProvider, ProviderSpec
from .providers import (
    ALL_PLACEHOLDERS,
    ALL_SPECS,
    ArkhamPlaceholder,
    CryptoQuantPlaceholder,
    GlassnodePlaceholder,
    NansenPlaceholder,
)

__all__ = [
    "ALL_PLACEHOLDERS",
    "ALL_SPECS",
    "ArkhamPlaceholder",
    "CryptoQuantPlaceholder",
    "GlassnodePlaceholder",
    "NansenPlaceholder",
    "NotIntegratedProvider",
    "ProviderSpec",
]
