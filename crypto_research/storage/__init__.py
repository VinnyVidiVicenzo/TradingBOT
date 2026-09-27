"""Local persistence for market data and exchange rule snapshots.

SQLite, from the standard library. A research store that one person runs on
one laptop does not need a server, and a single file is trivially hashable,
copyable and archivable alongside an experiment record.
"""

from .database import Database, open_database
from .repositories import (
    BarRepository,
    QuoteRepository,
    RawSnapshotRepository,
    RuleSnapshotRepository,
)

__all__ = [
    "BarRepository",
    "Database",
    "QuoteRepository",
    "RawSnapshotRepository",
    "RuleSnapshotRepository",
    "open_database",
]
