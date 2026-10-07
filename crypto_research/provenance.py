"""Content identities for the inputs and code actually used by a run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from .decimals import format_decimal


def rule_identity(rule_book) -> dict:
    snapshots = {s: asdict(rule_book.get(s).snapshot) for s in rule_book.symbols}
    text = json.dumps(snapshots, sort_keys=True, default=lambda v: format_decimal(v))
    return {"sha256": hashlib.sha256(text.encode()).hexdigest(), "snapshots": json.loads(text)}


def source_identity(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    for directory in ("crypto_research", "desktop_app", "external_intelligence", "paper_trading"):
        for path in sorted((root / directory).rglob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()
