"""The paper-trading zone can rehearse a plan and can never act on a real account.

Re-derived from the source on every run, like tests/test_safety.py and
tests/test_external_intelligence_safety.py. If one of these fails, this zone
has stopped being research-only.
"""

from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path

from crypto_research.safety import PAPER_TRADING_RULES, SECRET_ASSIGNMENT_RE, audit, audit_repository

REPO_ROOT = Path(__file__).parent.parent
PACKAGE = REPO_ROOT / "paper_trading"

STDLIB_ALLOWED = {
    "__future__", "argparse", "dataclasses", "decimal", "json", "pathlib", "sys", "typing",
}
# paper_trading contains no execution logic of its own: it only orchestrates the
# already-audited research engine. Its import surface should reflect that -- the
# engine's public client, config loader, backtester and registry, and nothing that
# authenticates, trades, or belongs to another zone.
FIRST_PARTY_ALLOWED = {
    "paper_trading",
    "crypto_research.binanceus.public_client",
    "crypto_research.config",
    "crypto_research.datafeed.download",
    "crypto_research.decimals",
    "crypto_research.errors",
    "crypto_research.experiments.registry",
    "crypto_research.experiments.runner",
    "crypto_research.storage.database",
    "crypto_research.storage.repositories",
    "crypto_research.timeutil",
}


def package_files():
    return [p for p in sorted(PACKAGE.rglob("*.py")) if "__pycache__" not in p.parts]


def imports_of(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module


class ZoneAuditTests(unittest.TestCase):
    def test_the_repository_audit_covers_the_zone_and_passes(self) -> None:
        report = audit_repository(REPO_ROOT)
        self.assertTrue(report.ok, report.summary())
        self.assertIn("paper-trading", report.zones)
        self.assertIn("cannot authenticate", report.zones["paper-trading"])

    def test_the_zone_rules_reject_signing_writes_credentials_and_the_wrong_venue(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "leak.py"
            bad.write_text(
                "import hmac\n"
                "requests" + ".post(" + '"https://api.binance.' + 'com/x")\n'
                'URL = "/api/v3/' + 'order"\n'
                "api_" + "key = " + '"' + "A" * 32 + '"\n',
                encoding="utf-8",
            )
            reasons = " | ".join(f.reason for f in audit(Path(tmp), rules=PAPER_TRADING_RULES).findings)
        for expected in ("signing", "HTTP POST", "Binance.com", "forbidden endpoint", "assigned to api_key"):
            self.assertIn(expected, reasons)

    def test_no_paid_or_third_party_dependency_is_declared(self) -> None:
        project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(project["dependencies"], [])


class ImportSurfaceTests(unittest.TestCase):
    def test_every_import_is_standard_library_or_an_approved_piece_of_the_engine(self) -> None:
        offenders = []
        for path in package_files():
            for name in imports_of(path):
                root = name.split(".")[0]
                if root in STDLIB_ALLOWED:
                    continue
                if name in FIRST_PARTY_ALLOWED or any(name.startswith(a + ".") for a in FIRST_PARTY_ALLOWED):
                    continue
                offenders.append(f"{path.relative_to(PACKAGE)} imports {name}")
        self.assertEqual(offenders, [])

    def test_nothing_reaches_account_tools_the_desktop_app_or_signing(self) -> None:
        for path in package_files():
            for name in imports_of(path):
                for banned in (
                    "account_tools", "desktop_app", "external_intelligence", "hmac",
                    "crypto_research.binanceus.transport", "crypto_research.safety",
                ):
                    self.assertFalse(name.startswith(banned), f"{path.name} imports {name}")

    def test_the_zone_reads_no_environment_variables_and_holds_no_credentials(self) -> None:
        for path in package_files():
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"os\.environ|getenv|keyring", path.name)
            self.assertIsNone(SECRET_ASSIGNMENT_RE.search(text), path.name)

    def test_no_function_is_named_like_a_trading_action(self) -> None:
        trade_verbs = {
            "buy", "sell", "market_buy", "market_sell", "limit_buy", "limit_sell",
            "place_order", "create_order", "submit_order", "send_order", "new_order",
            "cancel_order", "execute_trade", "withdraw", "transfer_funds",
        }
        offenders = []
        for path in package_files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.lower() in trade_verbs:
                    offenders.append(f"{path.name}:{node.name}")
        self.assertEqual(offenders, [])

    def test_dynamic_execution_and_dangerous_modules_are_absent(self) -> None:
        dynamic = {"eval", "exec", "compile", "__import__", "breakpoint"}
        dangerous = {"subprocess", "pickle", "marshal", "ctypes", "importlib", "shelve", "runpy", "code", "pty"}
        for path in package_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, dynamic, f"{path.name}:{node.lineno}")
            self.assertEqual({n for n in imports_of(path) if n.split(".")[0] in dangerous}, set(), path.name)


class ConfigCannotEnableSellingTests(unittest.TestCase):
    def test_the_shared_config_loader_still_refuses_allow_sell(self) -> None:
        # paper_trading resolves configs through crypto_research.config, which
        # already refuses allow_sell = true; this pins that the refusal still
        # reaches a config shaped like a rehearsal's.
        from crypto_research.config import parse_config
        from crypto_research.errors import ConfigError

        payload = {
            "data": {"symbols": ["BTCUSD"], "interval": "1d", "start": "2024-01-01T00:00:00Z",
                     "end": "2024-01-02T00:00:00Z"},
            "strategy": {"name": "benchmark0_hold"},
            "execution": {"allow_sell": True},
        }
        with self.assertRaises(ConfigError):
            parse_config(payload)


class ExampleConfigsTests(unittest.TestCase):
    """The shipped example configs are themselves audited: they are ordinary project files."""

    def test_the_example_configs_declare_no_secret_and_do_not_enable_selling(self) -> None:
        for name in ("paper_rehearsal_example.toml", "paper_benchmark_example.toml"):
            text = (REPO_ROOT / "configs" / name).read_text(encoding="utf-8")
            self.assertIsNone(SECRET_ASSIGNMENT_RE.search(text), name)
            self.assertNotIn("allow_sell", text)

    def test_the_two_example_configs_share_their_data_section(self) -> None:
        from crypto_research.config import load_config
        from paper_trading.plan import build_plan

        build_plan(
            load_config(REPO_ROOT / "configs" / "paper_rehearsal_example.toml"),
            load_config(REPO_ROOT / "configs" / "paper_benchmark_example.toml"),
        )  # does not raise


if __name__ == "__main__":
    unittest.main()
