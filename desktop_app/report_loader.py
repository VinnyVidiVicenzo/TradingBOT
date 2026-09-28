"""Turn existing plain-language write-ups into renderable blocks, unmodified.

No computation happens here -- this only reads files already written by past
research sessions (results/FINAL_REPORT.md, results/*_report.txt and the other
results/*.md write-ups) and classifies their lines for display.

Two source formats, handled separately:

* ``.md`` files are narrow Markdown: headings, bold/italic/code, links, flat
  bullets, numbered lines, block quotes, code fences, pipe tables and rules. A small line classifier
  covers that without pulling in a full Markdown engine.
* ``.txt`` reports are hand-aligned ASCII tables written by the analysis
  scripts. They are shown exactly as written -- interpreting Markdown in them
  would corrupt the alignment.

CONTEXT_FOR_CLAUDE.md is deliberately never offered: it is a briefing written
for an AI to continue the project, and talks about the reader in the third
person. results/FINAL_REPORT.md is the document written for the reader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BlockKind = Literal[
    "h1", "h2", "h3", "bullet", "numbered", "quote", "code", "table", "rule", "body", "blank"
]

# (start, end, style) character ranges within a block's text.
Span = tuple[int, int, str]

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*+]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\d+\.\s+.*$")
_RULE_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_FENCE_RE = re.compile(r"^\s*```")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_INLINE_RE = re.compile(
    r"\*\*(?P<bold>.+?)\*\*"
    r"|(?<![\w*])\*(?!\s)(?P<italic>[^*]+?)(?<!\s)\*(?![\w*])"
    r"|`(?P<code>[^`]+)`"
    r"|\[(?P<label>[^\]]+)\]\((?P<url>[^)]+)\)"
)


@dataclass(frozen=True, slots=True)
class Block:
    kind: BlockKind
    text: str = ""
    spans: tuple[Span, ...] = ()
    # Table rows, header first; only for kind == "table".
    cells: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class ReportEntry:
    key: str
    title: str
    description: str
    path: str  # relative to the repo root

    @property
    def is_markdown(self) -> bool:
        return self.path.lower().endswith(".md")


# A curated list, in reading order, with plain titles. results/ also holds raw
# *.csv data files and internal artifacts that were never written for a
# non-technical reader, so it is not globbed wholesale; only *_report.txt
# files the list doesn't know about are picked up automatically (see catalog).
# An entry whose file has gone missing is silently dropped.
CATALOG: tuple[ReportEntry, ...] = (
    ReportEntry(
        "final_report", "The full story, in plain words",
        "Every test and finding so far, explained for someone with no trading background.",
        "results/FINAL_REPORT.md",
    ),
    ReportEntry(
        "scenarios", "How each coin has done historically",
        "Best, worst and typical results for $200 held in each coin.",
        "results/scenarios_report.txt",
    ),
    ReportEntry(
        "news_events", "Big news events and the CLARITY Act",
        "How prices moved around major news, and whether reacting to it helped.",
        "results/event_study_report.txt",
    ),
    ReportEntry(
        "mix_vs_savings", "Crypto mixed with stocks or savings",
        "What a small slice of crypto does to a stock fund or a savings account.",
        "results/mix_vs_savings_report.txt",
    ),
    ReportEntry(
        "long_history", "A longer look back",
        "Stocks since 2000 and Bitcoin since 2014, including the crashes 2020-26 missed.",
        "results/long_history_report.txt",
    ),
    ReportEntry(
        "possible_futures", "5,000 possible futures",
        "Reshuffling history to see a wide range of what could plausibly happen next.",
        "results/possible_futures_report.txt",
    ),
    ReportEntry(
        "allocation", "Which mix would have worked best?",
        "66 mixes of crypto, stocks and cash -- and why the hindsight winner is a trap.",
        "results/optimal_allocation_report.txt",
    ),
    ReportEntry(
        "withdrawals", "What if you take money out along the way?",
        "Taking $5 a month out of crypto: does the order of good and bad years matter?",
        "results/withdrawal_risk_report.txt",
    ),
    ReportEntry(
        "fundamentals", "What's underneath the coins",
        "Real usage -- addresses, transactions, developer activity -- behind each price.",
        "results/fundamentals_report.txt",
    ),
    ReportEntry(
        "ownership", "Who actually holds each coin",
        "How spread out or concentrated ownership is, coin by coin.",
        "results/ownership_concentration.md",
    ),
    ReportEntry(
        "outside_forces", "Outside forces",
        "How crypto moves with stocks, interest rates and the dollar.",
        "results/big_forces_report.txt",
    ),
    ReportEntry(
        "correlation", "Do coins move together?",
        "How closely each coin tracks Bitcoin, and whether that changed over time.",
        "results/rolling_correlation_report.txt",
    ),
    ReportEntry(
        "digital_gold", "Is Bitcoin 'digital gold'?",
        "Bitcoin against gold, the fund-price story, and whether it has calmed down.",
        "results/macro_and_maturity_report.txt",
    ),
    ReportEntry(
        "leverage_flows", "Borrowing costs and new money",
        "What the cost of borrowing and new money arriving said about what came next.",
        "results/leverage_and_flows_report.txt",
    ),
    ReportEntry(
        "trading_cost", "The real cost of buying and selling",
        "What fees and thin markets actually cost, measured live.",
        "results/trading_cost_report.txt",
    ),
    ReportEntry(
        "valuation", "Is it expensive right now?",
        "Whether each coin looks stretched or cheap compared to its own history.",
        "results/valuation_report.txt",
    ),
    ReportEntry(
        "crowd_mood", "What the crowd is feeling",
        "Fear-and-greed readings and public attention, and what usually followed.",
        "results/crowd_mood_report.txt",
    ),
    ReportEntry(
        "etf_flows", "Where big investors' money is moving",
        "Real money flowing in and out of Bitcoin funds, day by day.",
        "results/etf_flows_farside.md",
    ),
    ReportEntry(
        "independent_review", "A second pair of eyes checked this",
        "An adversarial re-check of the analysis that found and fixed a real bug.",
        "results/independent_review.md",
    ),
    ReportEntry(
        "reference_verification", "Checking facts about the outside world",
        "Dates and claims from the news, checked against real sources.",
        "results/reference_verification.md",
    ),
)


def _inline(text: str) -> tuple[str, tuple[Span, ...]]:
    """Strip inline Markdown markers, returning plain text and where the styles fall."""
    spans: list[Span] = []
    out: list[str] = []
    length = 0
    pos = 0
    for match in _INLINE_RE.finditer(text):
        before = text[pos : match.start()]
        out.append(before)
        length += len(before)
        style = match.lastgroup
        if style == "url":  # a link: keep the words, drop the address
            inner, style = match.group("label"), None
        else:
            inner = match.group(style)
        out.append(inner)
        if style is not None:
            spans.append((length, length + len(inner), style))
        length += len(inner)
        pos = match.end()
    out.append(text[pos:])
    return "".join(out), tuple(spans)


_SENTENCE_END = (".", "!", "?", ":", ";")
_WRAP_MIN_LENGTH = 55


def _looks_hard_wrapped(previous_line: str) -> bool:
    """Was this line probably cut at the source file's margin rather than finished?

    Some of the write-ups wrap their paragraphs at about 75 characters; others
    use short consecutive lines on purpose (a title block). A line that is long
    and stops mid-sentence is treated as wrapped; anything else stays separate.
    """
    text = previous_line.strip().rstrip("*_`)\"'”’ ")
    return len(previous_line.strip()) >= _WRAP_MIN_LENGTH and not text.endswith(_SENTENCE_END)


def parse(text: str) -> list[Block]:
    """Classify Markdown lines into blocks."""
    blocks: list[Block] = []
    table_rows: list[tuple[str, ...]] = []
    pending_kind: BlockKind | None = None
    pending: list[str] = []

    def flush_pending() -> None:
        nonlocal pending_kind, pending
        if pending_kind is not None:
            body, spans = _inline(" ".join(pending))
            blocks.append(Block(pending_kind, body, spans))
        pending_kind, pending = None, []

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows:
            blocks.append(Block("table", cells=tuple(table_rows)))
            table_rows = []

    in_code = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if _FENCE_RE.match(line):
            flush_pending()
            flush_table()
            in_code = not in_code
            continue
        if in_code:
            blocks.append(Block("code", line))
            continue

        if len(stripped) > 1 and stripped.startswith("|") and stripped.endswith("|"):
            flush_pending()
            cells = [cell.strip() for cell in stripped[1:-1].split("|")]
            if not all(_SEPARATOR_CELL_RE.match(cell) for cell in cells):
                table_rows.append(tuple(_inline(cell)[0] for cell in cells))
            continue
        flush_table()

        if not stripped:
            flush_pending()
            blocks.append(Block("blank"))
        elif _RULE_RE.match(stripped):
            flush_pending()
            blocks.append(Block("rule"))
        elif header := _HEADER_RE.match(line):
            flush_pending()
            level = len(header.group(1))
            kind: BlockKind = "h1" if level == 1 else "h2" if level == 2 else "h3"
            body, spans = _inline(header.group(2))
            blocks.append(Block(kind, body, spans))
        elif quote := _QUOTE_RE.match(line):
            content = quote.group(1).strip()
            if not content:  # a bare ">" ends the quoted paragraph
                flush_pending()
            elif pending_kind == "quote" and _looks_hard_wrapped(pending[-1]):
                pending.append(content)
            else:
                flush_pending()
                pending_kind, pending = "quote", [content]
        elif bullet := _BULLET_RE.match(stripped):
            flush_pending()
            pending_kind, pending = "bullet", [bullet.group(1)]
        elif _NUMBERED_RE.match(stripped):
            flush_pending()
            pending_kind, pending = "numbered", [stripped]  # the number is part of the text
        else:
            continues = pending_kind is not None and (
                (pending_kind in ("bullet", "numbered") and line[:1].isspace())
                or (pending_kind == "body" and _looks_hard_wrapped(pending[-1]))
            )
            if continues:
                pending.append(stripped)
            else:
                flush_pending()
                pending_kind, pending = "body", [line]
    flush_pending()
    flush_table()
    return blocks


def parse_plain(text: str) -> list[Block]:
    """Fixed-width text reports: every line exactly as written."""
    return [
        Block("blank") if not line.strip() else Block("body", line.rstrip())
        for line in text.splitlines()
    ]


def _title_from_stem(stem: str) -> str:
    words = stem.removesuffix("_report").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def catalog(repo_root: Path) -> list[ReportEntry]:
    """The curated entries whose file exists, then any newer *_report.txt files."""
    curated = [entry for entry in CATALOG if (repo_root / entry.path).exists()]
    known = {entry.path for entry in CATALOG}
    extras: list[ReportEntry] = []
    results = repo_root / "results"
    if results.is_dir():
        for path in sorted(results.glob("*_report.txt")):
            relative = f"results/{path.name}"
            if relative in known:
                continue
            extras.append(
                ReportEntry(
                    path.stem, _title_from_stem(path.stem),
                    "More detail from the research.", relative,
                )
            )
    return curated + extras


def load(repo_root: Path, entry: ReportEntry) -> list[Block]:
    text = (repo_root / entry.path).read_text(encoding="utf-8")
    return parse(text) if entry.is_markdown else parse_plain(text)
