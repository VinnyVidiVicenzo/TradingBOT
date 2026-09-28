"""No direct signal shortcuts.

Nothing in this zone may turn an external observation into an action. These are
prohibited, in this package and in anything that consumes its features until the
relationship has been validated statistically out of sample::

    if whale_transfer_to_exchange:   sell()
    if whale_buy:                    buy()
    if rising_open_interest:         buy()
    if stablecoin_mint:              buy()
    if whale_ratio_high:             sell()
    if many_analysts_agree:          buy()

A flow, a funding rate, a liquidation or a mint has several possible meanings (an
internal move, custody, collateral, an OTC settlement, a treasury action ...).
Direction cannot be read off transfer size alone.

This module is a static check that makes the rule enforceable rather than
aspirational. It walks the syntax tree and reports

* any *call* to a trading verb (``buy``, ``sell``, ``place_order`` ...), and
* any trading call sitting inside a condition that tests something named like an
  external-data variable (``whale``, ``inflow``, ``funding`` ...).

The zone itself contains no trading call at all, so the first rule is the one that
fires; the second lets the same checker be pointed at strategy code later.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

TRADE_VERBS = frozenset(
    {
        "buy", "sell", "market_buy", "market_sell", "limit_buy", "limit_sell",
        "place_order", "create_order", "submit_order", "send_order", "new_order",
        "cancel_order", "execute_trade", "rebalance", "liquidate", "withdraw",
        "transfer_funds", "open_position", "close_position",
    }
)

TRIGGER_WORDS = (
    "whale", "inflow", "outflow", "liquidation", "funding", "open_interest", "stablecoin",
    "mint", "burn", "analyst", "exchange_flow", "net_flow", "transfer_to", "feature", "signal",
)


@dataclass(frozen=True, slots=True)
class Finding:
    file: str
    line: int
    kind: str  # "trading_call" | "conditional_shortcut"
    detail: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.kind}: {self.detail}"


def _callee_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _mentions_trigger(node: ast.AST) -> str | None:
    for child in ast.walk(node):
        text = None
        if isinstance(child, ast.Name):
            text = child.id
        elif isinstance(child, ast.Attribute):
            text = child.attr
        elif isinstance(child, ast.Constant) and isinstance(child.value, str):
            text = child.value
        if text:
            lowered = text.lower()
            for word in TRIGGER_WORDS:
                if word in lowered:
                    return word
    return None


def _trading_calls(nodes: Iterable[ast.AST]) -> Iterator[ast.Call]:
    for root in nodes:
        for child in ast.walk(root):
            if isinstance(child, ast.Call) and _callee_name(child) in TRADE_VERBS:
                yield child


def find_direct_signal_shortcuts(source: str, filename: str = "<string>") -> list[Finding]:
    tree = ast.parse(source, filename=filename)
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()

    def add(line: int, kind: str, detail: str) -> None:
        key = (line, kind)
        if key not in seen:
            seen.add(key)
            findings.append(Finding(filename, line, kind, detail))

    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp)):
            trigger = _mentions_trigger(node.test)
            if trigger is not None:
                branches = [*node.body, *node.orelse] if not isinstance(node, ast.IfExp) else [node.body, node.orelse]
                for call in _trading_calls(branches):
                    add(
                        call.lineno, "conditional_shortcut",
                        f"{_callee_name(call)}() is called under a condition on '{trigger}'",
                    )
    for call in _trading_calls([tree]):
        add(call.lineno, "trading_call", f"{_callee_name(call)}() is a trading action")
    return sorted(findings, key=lambda f: (f.file, f.line, f.kind))


def scan_package(root: Path) -> list[Finding]:
    """Every finding across the ``.py`` files under ``root``."""
    findings: list[Finding] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        findings.extend(find_direct_signal_shortcuts(path.read_text(encoding="utf-8"), str(path)))
    return findings
