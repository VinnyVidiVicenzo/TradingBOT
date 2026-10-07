"""Reading a payload into records, without interpreting them.

Supports the three shapes a user-provided export realistically takes: JSON
Lines, a JSON document (an array, or an object holding an array under a key the
provider names), and CSV with a header row. A line that cannot be parsed becomes
an :class:`UnparseableRecord` so it is quarantined with a reason instead of
silently vanishing or aborting the whole file.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any, Sequence

from ..errors import ProviderSchemaError


@dataclass(frozen=True, slots=True)
class UnparseableRecord:
    text: str


def parse_records(
    text: str, *, suffix: str, envelope_keys: Sequence[str] = ()
) -> list[tuple[int, Any]]:
    """``[(record_index, record), ...]`` in file order."""
    kind = suffix.lower()
    if kind in (".jsonl", ".ndjson"):
        return _parse_jsonl(text)
    if kind == ".json":
        return _parse_json(text, envelope_keys)
    if kind == ".csv":
        return _parse_csv(text)
    raise ProviderSchemaError(f"unsupported payload type {suffix!r} (use .jsonl, .json or .csv)")


def _parse_jsonl(text: str) -> list[tuple[int, Any]]:
    out: list[tuple[int, Any]] = []
    index = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            out.append((index, json.loads(stripped)))
        except (json.JSONDecodeError, RecursionError, ValueError):
            out.append((index, UnparseableRecord(stripped[:200])))
        index += 1
    return out


def _parse_json(text: str, envelope_keys: Sequence[str]) -> list[tuple[int, Any]]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ProviderSchemaError("invalid or excessively complex JSON document") from exc
    if isinstance(data, list):
        return list(enumerate(data))
    if isinstance(data, dict):
        for key in envelope_keys:
            if isinstance(data.get(key), list):
                return list(enumerate(data[key]))
        return [(0, data)]
    raise ProviderSchemaError("JSON payload is neither an array nor an object")


def _parse_csv(text: str) -> list[tuple[int, Any]]:
    reader = csv.DictReader(io.StringIO(text))
    try:
        if not reader.fieldnames:
            raise ProviderSchemaError("CSV payload has no header row")
        return [(index, UnparseableRecord("CSV row has surplus columns") if None in row else dict(row))
                for index, row in enumerate(reader)]
    except csv.Error as exc:
        raise ProviderSchemaError("invalid CSV document") from exc
