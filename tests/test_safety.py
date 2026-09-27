"""The safety guarantee, re-derived from the source on every test run.

If any assertion in this file fails, V1 has stopped being research-only and
the change that caused it must be reverted or deliberately re-scoped.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from crypto_research.backtest.execution import ExecutionConfig
from crypto_research.binanceus.constants import BASE_URL, PUBLIC_ENDPOINTS
from crypto_research.binanceus.public_client import BinanceUSPublicClient
from crypto_research.binanceus.transport import PublicTransport
from crypto_research.errors import TransportError
from crypto_research.safety import (
    RESEARCH_RULES,
    SECRET_ASSIGNMENT_RE,
    audit,
    audit_repository,
)

REPO_ROOT = Path(__file__).parent.parent
PACKAGE_ROOT = REPO_ROOT / "crypto_research"

PRIVATE_ENDPOINT_FRAGMENTS = (
    "/order", "/account", "/myTrades", "/openOrders", "/allOrders",
    "/userDataStream", "/withdraw", "/capital", "/asset",
)

ORDER_VERBS = (
    "place_order", "create_order", "new_order", "submit_order", "send_order",
    "cancel_order", "amend_order", "replace_order", "post_order",
    "withdraw_funds", "transfer",
)


class SourceAuditTests(unittest.TestCase):
    def test_the_repository_passes_the_zone_audit(self) -> None:
        report = audit_repository(REPO_ROOT)
        self.assertTrue(report.ok, report.summary())
        self.assertGreater(report.files_scanned, 20)

    def test_the_research_zone_alone_is_free_of_signing_and_credentials(self) -> None:
        # The strong claim, checked on its own: the package a backtest run
        # executes has no way to authenticate at all.
        report = audit(PACKAGE_ROOT, rules=RESEARCH_RULES)
        self.assertTrue(report.ok, report.summary())

    def test_the_audit_actually_detects_a_violation(self) -> None:
        # A guard that cannot fail is not a guard. Feed it something bad.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "leak.py"
            # Assembled from fragments so that this file does not itself
            # contain the literal string the audit is meant to catch.
            forbidden = "/api/v3/" + "order"
            bad.write_text(
                "import hmac\n" f'URL = "https://api.binance.us{forbidden}"\n',
                encoding="utf-8",
            )
            report = audit(Path(tmp))
            self.assertFalse(report.ok)
            reasons = {finding.reason for finding in report.findings}
            self.assertTrue(
                any("signing" in reason for reason in reasons), reasons
            )
            self.assertTrue(
                any("forbidden endpoint" in reason for reason in reasons), reasons
            )

    def test_the_secret_detector_matches_a_value_not_a_mention(self) -> None:
        self.assertIsNone(
            SECRET_ASSIGNMENT_RE.search("# there is deliberately no API_SECRET here")
        )
        self.assertIsNotNone(
            SECRET_ASSIGNMENT_RE.search("API_SECRET=" + "a" * 32)  # safety-audit: allow
        )

    def test_env_example_contains_no_credential_value(self) -> None:
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIsNone(SECRET_ASSIGNMENT_RE.search(text))


class EndpointSurfaceTests(unittest.TestCase):
    def test_only_public_endpoints_are_permitted(self) -> None:
        for endpoint in PUBLIC_ENDPOINTS:
            for fragment in PRIVATE_ENDPOINT_FRAGMENTS:
                self.assertNotIn(
                    fragment, endpoint,
                    f"{endpoint} looks like a private endpoint",
                )

    def test_the_transport_refuses_an_unlisted_path(self) -> None:
        transport = PublicTransport(sleep=lambda _s: None)
        with self.assertRaises(TransportError):
            transport.get("/api/v3/anything-else")

    def test_the_base_url_is_binance_us_and_not_binance_com(self) -> None:
        self.assertEqual(BASE_URL, "https://api.binance.us")
        self.assertNotIn(".com", BASE_URL)


class NoOrderApiTests(unittest.TestCase):
    def test_the_public_client_exposes_no_order_method(self) -> None:
        names = {name.lower() for name in dir(BinanceUSPublicClient)}
        for verb in ORDER_VERBS:
            self.assertNotIn(verb, names)

    def test_no_module_in_the_package_defines_an_order_placing_function(self) -> None:
        offenders: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.lower() in ORDER_VERBS:
                        offenders.append(f"{path.name}:{node.name}")
        self.assertEqual(offenders, [])

    def test_the_package_has_no_third_party_dependencies(self) -> None:
        # A financial tool with no dependency tree cannot be compromised
        # through one. Standard library only, verified by import inspection.
        allowed_roots = {
            "crypto_research", "__future__", "argparse", "ast", "csv", "dataclasses",
            "datetime", "decimal", "email", "enum", "hashlib", "heapq", "itertools",
            "json", "os", "pathlib", "platform", "random", "re", "sqlite3",
            "subprocess", "sys", "time", "tomllib", "types", "typing", "urllib",
            "contextlib", "traceback", "collections", "functools", "math",
            # First-party sibling subproject, referenced lazily by safety.py
            # only so the audit can report its endpoint whitelist.
            "account_tools",
        }
        found: set[str] = set()
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.level == 0 and node.module:
                        found.add(node.module.split(".")[0])
        self.assertEqual(found - allowed_roots, set())


class SellGateTests(unittest.TestCase):
    def test_selling_is_off_by_default(self) -> None:
        self.assertFalse(ExecutionConfig().allow_sell)

    def test_a_config_file_cannot_switch_selling_on(self) -> None:
        import tomllib

        from crypto_research.config import parse_config
        from crypto_research.errors import ConfigError

        text = """
[experiment]
name = "t"
[data]
symbols = ["BTCUSD"]
interval = "1d"
start = "2024-01-01T00:00:00Z"
end = "2024-02-01T00:00:00Z"
[strategy]
name = "benchmark0_hold"
[execution]
allow_sell = true
"""
        with self.assertRaises(ConfigError):
            parse_config(tomllib.loads(text))


class DocumentationTests(unittest.TestCase):
    """The safety claims have to actually be written down somewhere."""

    def test_the_required_documents_exist(self) -> None:
        for name in (
            "ARCHITECTURE.md", "DATA_MODEL.md", "BACKTESTING.md",
            "SAFETY.md", "EXPERIMENTS.md",
        ):
            with self.subTest(document=name):
                self.assertTrue((REPO_ROOT / "docs" / "trading-bot" / name).exists())

    def test_safety_doc_states_the_key_prohibitions(self) -> None:
        text = (REPO_ROOT / "docs" / "trading-bot" / "SAFETY.md").read_text(
            encoding="utf-8"
        ).lower()
        for claim in (
            "live execution",
            "untrusted",
            "capital limit",
            "no model",
        ):
            with self.subTest(claim=claim):
                self.assertIn(claim, text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
