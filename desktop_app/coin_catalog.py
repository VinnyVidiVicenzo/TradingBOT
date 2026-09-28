"""Which coins can actually be offered, checked live against the local store.

Split into a DB-taking core (unit-tested against a throwaway in-memory
database, never the real one) and a path-taking wrapper (what screens call).
The *availability* of a coin is always checked live via BarRepository, the
same class ``python -m crypto_research data coverage`` itself reads from --
nothing here is hardcoded as "available."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from crypto_research.storage.database import Database, open_database
from crypto_research.storage.repositories import BarRepository
from crypto_research.timeutil import to_iso

QUOTE_ASSET = "USDT"
INTERVAL = "1d"

# Exactly the coins results/ and CONTEXT_FOR_CLAUDE.md already cover in plain
# language. Offering more would show coins this app never otherwise talks
# about; offering fewer would leave out ones already researched.
FRIENDLY_NAMES: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XRP": "XRP",
    "DOGE": "Dogecoin",
    "BNB": "BNB",
    "ZEC": "Zcash",
}


@dataclass(frozen=True, slots=True)
class CoinOption:
    base_asset: str
    display_name: str
    symbol: str
    bar_count: int
    first_open_ms: int
    last_open_ms: int

    @property
    def first_open_date(self) -> str:
        return to_iso(self.first_open_ms)[:10]

    @property
    def last_open_date(self) -> str:
        return to_iso(self.last_open_ms)[:10]


def discover_available_coins_from_db(db: Database) -> list[CoinOption]:
    bars = BarRepository(db)
    options: list[CoinOption] = []
    for base, name in FRIENDLY_NAMES.items():
        symbol = f"{base}{QUOTE_ASSET}"
        count, first_open, last_open = bars.coverage(symbol, INTERVAL)
        if count:
            options.append(CoinOption(base, name, symbol, count, first_open, last_open))
    return options


def discover_available_coins(db_path: str) -> list[CoinOption]:
    # open_database creates the file (and its folder) when missing. Looking at
    # what is available must not leave an empty database behind on a machine
    # that has never downloaded any prices.
    if not Path(db_path).is_file():
        return []
    with open_database(db_path) as db:
        return discover_available_coins_from_db(db)


def overlapping_range(options: list[CoinOption]) -> tuple[int, int] | None:
    """(first, last) bar open times, in ms, between which *every* coin has history.

    Only the outer edges: a coin can still have a hole in the middle (XRP does),
    which the sweep detects per window rather than assuming continuity here.
    """
    if not options:
        return None
    lo = max(option.first_open_ms for option in options)
    hi = min(option.last_open_ms for option in options)
    if hi <= lo:
        return None
    return lo, hi
