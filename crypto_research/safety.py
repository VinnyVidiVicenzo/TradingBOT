"""A machine-checkable statement that V1 cannot trade.

The guarantee "there is no live order path" is only worth something if it is
verified rather than asserted. :func:`audit` re-derives it from the source on
demand and a test runs it on every commit, so the day somebody adds request
signing or a private endpoint, the build says so.

A line may opt out with a trailing ``# safety-audit: allow`` marker. That is
for the few places that have to *name* a forbidden thing in order to refuse
it -- the transport rejecting a credential header, for instance. The marker
is deliberately noisy to read and trivial to grep for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

PACKAGE_ROOT = Path(__file__).parent

ALLOW_MARKER = "safety-audit: allow"

# Private Binance.US endpoints. None of these may appear anywhere.
FORBIDDEN_ENDPOINTS: tuple[str, ...] = (
    "/api/v3/order",
    "/api/v3/openOrders",
    "/api/v3/allOrders",
    "/api/v3/account",
    "/api/v3/myTrades",
    "/api/v3/orderList",
    "/api/v3/userDataStream",
    "/sapi/v1/asset",
    "/sapi/v1/capital",
    "/sapi/v1/withdraw",
)

# Signing and credential machinery. Their absence is what makes an
# authenticated request impossible rather than merely unimplemented.
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^\s*import\s+hmac\b", "HMAC signing would enable authenticated requests"),
    (r"\bhmac\.new\s*\(", "HMAC signing would enable authenticated requests"),
    (r"X-MBX-APIKEY", "API-key header"),
    (r"\bmethod\s*=\s*[\"']POST[\"']", "HTTP POST"),
    (r"\bmethod\s*=\s*[\"']DELETE[\"']", "HTTP DELETE"),
    (r"\bmethod\s*=\s*[\"']PUT[\"']", "HTTP PUT"),
    (r"\.post\s*\(", "HTTP POST"),
    (r"api\.binance\.com", "Binance.com is a different exchange"),
    (r"stream\.binance\.com", "Binance.com is a different exchange"),
    (r"testnet\.binance", "Binance testnet is not Binance.US"),
    (r"vision\.binance", "Binance testnet is not Binance.US"),
)

# An assignment of something that looks like a real credential value. Matches
# on the value, not the word, so documentation that says "there is no
# BINANCE_API_SECRET here" does not trip it.
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?secret|secret[_-]?key|api[_-]?key|private[_-]?key)\s*[:=]\s*"
    r"[\"']?([A-Za-z0-9/+=_-]{16,})"
)

SCANNED_SUFFIXES = frozenset({".py", ".toml", ".json", ".md", ".txt", ".cfg", ".ini", ".yml", ".yaml"})
SKIPPED_DIRECTORIES = frozenset({".git", ".venv", "venv", "__pycache__", "runs", "node_modules"})

# This module necessarily spells out the things it forbids.
EXEMPT_FILES: frozenset[str] = frozenset({"safety.py"})


@dataclass(slots=True)
class SafetyFinding:
    file: str
    line: int
    pattern: str
    reason: str
    text: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.reason} -- {self.text.strip()[:120]}"


@dataclass(slots=True)
class SafetyReport:
    findings: list[SafetyFinding] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            return (
                f"SAFE: {self.files_scanned} file(s) scanned. No private endpoints, "
                "no request signing, no credential values, no Binance.com or "
                "testnet references."
            )
        lines = [f"UNSAFE: {len(self.findings)} finding(s):"]
        lines.extend(f"  {finding}" for finding in self.findings)
        return "\n".join(lines)


def audit(root: Path | None = None, *, extra_roots: Iterable[Path] = ()) -> SafetyReport:
    """Scan for anything that could reach a live order path or leak a secret."""
    package_root = root or PACKAGE_ROOT
    report = SafetyReport()
    compiled = [(re.compile(p, re.IGNORECASE), reason) for p, reason in FORBIDDEN_PATTERNS]

    for path in _iter_files(package_root, extra_roots):
        report.files_scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable file
            continue
        label = _label(path, package_root)
        for number, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue
            if path.suffix == ".py":
                for endpoint in FORBIDDEN_ENDPOINTS:
                    if endpoint in line:
                        report.findings.append(
                            SafetyFinding(label, number, endpoint,
                                          f"private endpoint {endpoint}", line)
                        )
                for regex, reason in compiled:
                    if regex.search(line):
                        report.findings.append(
                            SafetyFinding(label, number, regex.pattern, reason, line)
                        )
            match = SECRET_ASSIGNMENT_RE.search(line)
            if match:
                report.findings.append(
                    SafetyFinding(
                        label, number, "credential assignment",
                        f"a value is assigned to {match.group(1)}",
                        line[: match.start(2)] + "<redacted>",
                    )
                )
    return report


def _iter_files(package_root: Path, extra_roots: Iterable[Path]) -> Sequence[Path]:
    roots = [package_root, *extra_roots]
    seen: dict[Path, None] = {}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            seen.setdefault(root, None)
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix not in SCANNED_SUFFIXES:
                continue
            if any(part in SKIPPED_DIRECTORIES for part in path.parts):
                continue
            if path.name in EXEMPT_FILES:
                continue
            seen.setdefault(path, None)
    return list(seen)


def _label(path: Path, package_root: Path) -> str:
    try:
        return str(path.relative_to(package_root.parent))
    except ValueError:
        return str(path)


def permitted_endpoints() -> Sequence[str]:
    from .binanceus.constants import PUBLIC_ENDPOINTS

    return sorted(PUBLIC_ENDPOINTS)
