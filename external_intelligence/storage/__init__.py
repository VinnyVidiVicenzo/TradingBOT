"""The external-intelligence store and its repositories."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .database import DEFAULT_PATH, IntelligenceDatabase, open_intelligence_database
from .repositories import (
    AttributionRepository,
    CanonicalRepository,
    CoverageRepository,
    CursorRepository,
    EventRepository,
    HealthRepository,
    QuarantineRepository,
    RawPayloadRepository,
)


@dataclass(slots=True)
class IntelligenceStore:
    """One handle over every repository, for callers that want all of them."""

    db: IntelligenceDatabase
    raw: RawPayloadRepository
    events: EventRepository
    quarantine: QuarantineRepository
    attributions: AttributionRepository
    coverage: CoverageRepository
    health: HealthRepository
    canonical: CanonicalRepository
    cursors: CursorRepository

    @classmethod
    def from_database(cls, db: IntelligenceDatabase) -> "IntelligenceStore":
        return cls(
            db=db,
            raw=RawPayloadRepository(db),
            events=EventRepository(db),
            quarantine=QuarantineRepository(db),
            attributions=AttributionRepository(db),
            coverage=CoverageRepository(db),
            health=HealthRepository(db),
            canonical=CanonicalRepository(db),
            cursors=CursorRepository(db),
        )

    @classmethod
    def open(cls, path: str | Path = DEFAULT_PATH) -> "IntelligenceStore":
        return cls.from_database(open_intelligence_database(path))

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "IntelligenceStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
