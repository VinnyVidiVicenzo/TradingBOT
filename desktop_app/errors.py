"""Plain-language translation for anything that can go wrong.

Nothing in this module ever shows a raw traceback or a file path to the person
using the app. Known engine messages (runner.py, datafeed/loader.py,
backtest/engine.py) are mapped to a plain explanation; anything else gets a
generic one that says, truthfully, that nothing was bought or sold.
"""

from __future__ import annotations

from crypto_research.errors import CryptoResearchError

# Fragments of real messages raised by the engine, matched by substring so
# small wording differences (symbol names, counts) don't matter.
_KNOWN_FRAGMENTS: tuple[tuple[str, str], ...] = (
    (
        "no decision times fall inside the run window",
        "There isn't enough saved price history for these dates. Try different coins.",
    ),
    (
        "no exchangeInfo snapshots",
        "The saved trading-rules data is missing. Run the setup steps in the "
        "README's Quick Start first.",
    ),
    (
        "no Binance.US trading rules stored for",
        "One of the coins picked doesn't have saved trading rules yet. Try a "
        "different coin.",
    ),
    (
        "bars stored for",
        "One of the coins picked doesn't have saved price history for these "
        "dates yet. Try different coins.",
    ),
    (
        "market data has",
        "There's a gap in the saved price history for these dates. Try "
        "different coins or a different date range.",
    ),
)


def explain(exc: Exception) -> str:
    text = str(exc)
    for fragment, friendly in _KNOWN_FRAGMENTS:
        if fragment in text:
            return friendly
    if isinstance(exc, CryptoResearchError):
        return f"This plan couldn't be set up: {text}"
    return (
        "Something unexpected went wrong. Nothing was bought or sold -- this "
        f"tool only ever does practice math. Technical detail: {text}"
    )
