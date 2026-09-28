"""A machine-checkable statement of what each part of this repository can do.

The repository has two zones with two different, separately-verified claims:

* **research** (``crypto_research/``) -- cannot authenticate at all. No signing,
  no credential handling, no private endpoints, GET only. This is the guarantee
  the backtester rests on: it cannot place an order because it has no way to
  authenticate, not because it declines to.

* **account-tools** (``account_tools/``) -- may authenticate, and does, to read
  commission rates and balances. It may not trade or move funds: no order,
  cancel, withdraw, transfer or convert endpoint, and GET only.

* **support** (tests, configs, docs) -- may name any of it in prose, but may
  never contain a credential value.

Keeping these separate is what lets the strong claim stay strong. Merging them
into one "repo is safe" check would mean the presence of signing code anywhere
either fails the build or quietly stops being reported.

A line may opt out with a trailing ``# safety-audit: allow`` marker, for the
few places that must *name* a forbidden thing in order to refuse it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

PACKAGE_ROOT = Path(__file__).parent
REPO_ROOT = PACKAGE_ROOT.parent

ALLOW_MARKER = "safety-audit: allow"

# Endpoints that place, change or settle an order, or move funds. Forbidden
# everywhere in this repository, in every zone, without exception.
TRADING_ENDPOINTS: tuple[str, ...] = (
    "/api/v3/order",
    "/api/v3/openOrders",
    "/api/v3/allOrders",
    "/api/v3/orderList",
    "/api/v3/userDataStream",
    "/sapi/v1/capital/withdraw",
    "/sapi/v1/asset/transfer",
    "/sapi/v1/convert",
    "/sapi/v1/otc",
)

# Private endpoints that merely *read* account state. Forbidden in the research
# zone, permitted in account-tools.
ACCOUNT_READ_ENDPOINTS_FORBIDDEN_IN_RESEARCH: tuple[str, ...] = (
    "/api/v3/account",
    "/api/v3/myTrades",
    "/api/v3/myFilters",
    "/sapi/v1/asset",
    "/sapi/v1/account",
    "/sapi/v1/capital",
)

# Pointing at the wrong venue. Forbidden everywhere: Binance.com is a different
# exchange with different pairs, filters and fees.
WRONG_VENUE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"api\.binance\.com", "Binance.com is a different exchange"),
    (r"stream\.binance\.com", "Binance.com is a different exchange"),
    (r"testnet\.binance", "Binance testnet is not Binance.US"),
    (r"vision\.binance", "Binance testnet is not Binance.US"),
)

# Anything that writes. Forbidden everywhere: both zones are read-only.
WRITE_METHOD_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\bmethod\s*=\s*[\"']POST[\"']", "HTTP POST"),
    (r"\bmethod\s*=\s*[\"']DELETE[\"']", "HTTP DELETE"),
    (r"\bmethod\s*=\s*[\"']PUT[\"']", "HTTP PUT"),
    (r"\.post\s*\(", "HTTP POST"),
    (r"\.delete\s*\(", "HTTP DELETE"),
)

# Signing and credential machinery. Forbidden in the research zone only; their
# absence there is what makes an authenticated request impossible rather than
# merely unimplemented.
SIGNING_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"^\s*import\s+hmac\b", "request signing would enable authentication"),
    (r"\bhmac\.new\s*\(", "request signing would enable authentication"),
    (r"X-MBX-APIKEY", "API-key header"),
)

# Kept for compatibility with earlier callers.
FORBIDDEN_ENDPOINTS: tuple[str, ...] = (
    TRADING_ENDPOINTS + ACCOUNT_READ_ENDPOINTS_FORBIDDEN_IN_RESEARCH
)
FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    SIGNING_PATTERNS + WRITE_METHOD_PATTERNS + WRONG_VENUE_PATTERNS
)

# An assignment of something that looks like a real credential value. Matches
# on the value, not the word, so documentation saying "there is no secret here"
# does not trip it. Checked in every zone.
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?secret|secret[_-]?key|api[_-]?key|private[_-]?key)\s*[:=]\s*"
    r"[\"']?([A-Za-z0-9/+=_-]{16,})"
)

SCANNED_SUFFIXES = frozenset(
    {".py", ".toml", ".json", ".md", ".txt", ".cfg", ".ini", ".yml", ".yaml"}
)
SKIPPED_DIRECTORIES = frozenset(
    {".git", ".venv", "venv", "__pycache__", "runs", "node_modules"}
)

# This module necessarily spells out the things it forbids.
EXEMPT_FILES: frozenset[str] = frozenset({"safety.py"})


@dataclass(frozen=True, slots=True)
class RuleSet:
    """The claim being made about one zone."""

    zone: str
    claim: str
    forbidden_endpoints: tuple[str, ...]
    forbidden_patterns: tuple[tuple[str, str], ...]


RESEARCH_RULES = RuleSet(
    zone="research",
    claim="cannot authenticate: no signing, no credentials, no private endpoints",
    forbidden_endpoints=TRADING_ENDPOINTS + ACCOUNT_READ_ENDPOINTS_FORBIDDEN_IN_RESEARCH,
    forbidden_patterns=SIGNING_PATTERNS + WRITE_METHOD_PATTERNS + WRONG_VENUE_PATTERNS,
)

ACCOUNT_TOOLS_RULES = RuleSet(
    zone="account-tools",
    claim="authenticates to read only: cannot trade, cancel, withdraw or transfer",
    forbidden_endpoints=TRADING_ENDPOINTS,
    forbidden_patterns=WRITE_METHOD_PATTERNS + WRONG_VENUE_PATTERNS,
)

SUPPORT_RULES = RuleSet(
    zone="support",
    claim="contains no credential values and names no trading endpoint",
    forbidden_endpoints=TRADING_ENDPOINTS,
    forbidden_patterns=WRONG_VENUE_PATTERNS,
)

# The desktop GUI (desktop_app/) only ever calls into the research engine, so
# it carries exactly the same claim as RESEARCH_RULES rather than the weaker
# SUPPORT_RULES: it must be structurally unable to authenticate, not merely
# undocumented for doing so.
DESKTOP_APP_RULES = RuleSet(
    zone="desktop-app",
    claim="cannot authenticate: no signing, no credentials, no private endpoints",
    forbidden_endpoints=TRADING_ENDPOINTS + ACCOUNT_READ_ENDPOINTS_FORBIDDEN_IN_RESEARCH,
    forbidden_patterns=SIGNING_PATTERNS + WRITE_METHOD_PATTERNS + WRONG_VENUE_PATTERNS,
)

# External market intelligence (external_intelligence/) reads and analyses third-party
# data. It makes the same claim as the research engine, so it is held to the same
# rules: no signing, no credentials, no private endpoints, no writes. It must also
# never reach an order -- that half is enforced by tests/test_external_intelligence_safety.py,
# which walks every import.
EXTERNAL_INTELLIGENCE_RULES = RuleSet(
    zone="external-intel",
    claim="reads external data only: cannot authenticate, trade or move funds",
    forbidden_endpoints=TRADING_ENDPOINTS + ACCOUNT_READ_ENDPOINTS_FORBIDDEN_IN_RESEARCH,
    forbidden_patterns=SIGNING_PATTERNS + WRITE_METHOD_PATTERNS + WRONG_VENUE_PATTERNS,
)

# Paper trading (paper_trading/) rehearses a plan against live prices with pretend
# money. It carries exactly the research engine's claim, for the strongest possible
# reason: it contains no execution logic of its own at all. It imports the research
# engine's own public client and its own backtester unchanged, and produces no
# SimulatedOrder that was not already produced by code this audit already covers.
PAPER_TRADING_RULES = RuleSet(
    zone="paper-trading",
    claim="cannot authenticate: no signing, no credentials, no private endpoints",
    forbidden_endpoints=TRADING_ENDPOINTS + ACCOUNT_READ_ENDPOINTS_FORBIDDEN_IN_RESEARCH,
    forbidden_patterns=SIGNING_PATTERNS + WRITE_METHOD_PATTERNS + WRONG_VENUE_PATTERNS,
)


@dataclass(slots=True)
class SafetyFinding:
    file: str
    line: int
    pattern: str
    reason: str
    text: str
    zone: str = ""

    def __str__(self) -> str:
        where = f"[{self.zone}] " if self.zone else ""
        return f"{where}{self.file}:{self.line}: {self.reason} -- {self.text.strip()[:110]}"


@dataclass(slots=True)
class SafetyReport:
    findings: list[SafetyFinding] = field(default_factory=list)
    files_scanned: int = 0
    zones: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        if self.ok:
            lines = [f"SAFE: {self.files_scanned} file(s) scanned."]
            for zone, claim in self.zones.items():
                lines.append(f"  {zone:<14} {claim}")
            return "\n".join(lines)
        lines = [f"UNSAFE: {len(self.findings)} finding(s):"]
        lines.extend(f"  {finding}" for finding in self.findings)
        return "\n".join(lines)


def audit(
    root: Path | None = None,
    *,
    extra_roots: Iterable[Path] = (),
    rules: RuleSet = RESEARCH_RULES,
) -> SafetyReport:
    """Scan one zone against one rule set."""
    scan_root = root or PACKAGE_ROOT
    report = SafetyReport(zones={rules.zone: rules.claim})
    compiled = [
        (re.compile(p, re.IGNORECASE), reason) for p, reason in rules.forbidden_patterns
    ]

    for path in _iter_files(scan_root, extra_roots):
        report.files_scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable file
            continue
        label = _label(path)
        for number, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue
            if path.suffix == ".py":
                for endpoint in rules.forbidden_endpoints:
                    if endpoint in line:
                        report.findings.append(
                            SafetyFinding(
                                label, number, endpoint,
                                f"forbidden endpoint {endpoint}", line, rules.zone,
                            )
                        )
                for regex, reason in compiled:
                    if regex.search(line):
                        report.findings.append(
                            SafetyFinding(
                                label, number, regex.pattern, reason, line, rules.zone
                            )
                        )
            match = SECRET_ASSIGNMENT_RE.search(line)
            if match:
                report.findings.append(
                    SafetyFinding(
                        label, number, "credential assignment",
                        f"a value is assigned to {match.group(1)}",
                        line[: match.start(2)] + "<redacted>", rules.zone,
                    )
                )
    return report


def audit_repository(repo_root: Path | None = None) -> SafetyReport:
    """Audit every zone and merge the result.

    This is what ``python -m crypto_research safety-check`` runs.
    """
    root = repo_root or REPO_ROOT
    combined = SafetyReport()

    zones: Sequence[tuple[RuleSet, Path, Sequence[Path]]] = (
        (RESEARCH_RULES, root / "crypto_research", ()),
        (ACCOUNT_TOOLS_RULES, root / "account_tools", ()),
        (DESKTOP_APP_RULES, root / "desktop_app", ()),
        (EXTERNAL_INTELLIGENCE_RULES, root / "external_intelligence", ()),
        (PAPER_TRADING_RULES, root / "paper_trading", ()),
        (
            SUPPORT_RULES,
            root / "tests",
            (
                root / "configs",
                root / "docs",
                root / ".env.example",
                root / "README.md",
                root / "pyproject.toml",
            ),
        ),
    )

    for rules, zone_root, extras in zones:
        if not zone_root.exists():
            continue
        report = audit(
            zone_root,
            extra_roots=[p for p in extras if p.exists()],
            rules=rules,
        )
        combined.findings.extend(report.findings)
        combined.files_scanned += report.files_scanned
        combined.zones[rules.zone] = rules.claim
    return combined


def _iter_files(scan_root: Path, extra_roots: Iterable[Path]) -> Sequence[Path]:
    roots = [scan_root, *extra_roots]
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


def _label(path: Path) -> str:
    for base in (REPO_ROOT, PACKAGE_ROOT.parent):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def permitted_endpoints() -> Mapping[str, Sequence[str]]:
    """What each zone is allowed to reach, straight from the whitelists."""
    from .binanceus.constants import PUBLIC_ENDPOINTS

    zones: dict[str, Sequence[str]] = {"research (public, unauthenticated)": sorted(PUBLIC_ENDPOINTS)}
    try:
        from account_tools.signing import ACCOUNT_READ_ENDPOINTS

        zones["account-tools (signed, read-only)"] = sorted(ACCOUNT_READ_ENDPOINTS)
    except ImportError:  # pragma: no cover - subproject absent
        pass
    return zones
