"""desktop_app must be visible to the same audit crypto_research runs on itself.

Mirrors the technique tests/test_safety.py already uses on crypto_research/
itself (AST-walking every import) so the same "enforced, not promised"
guarantee extends to this package.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from crypto_research.safety import (
    ALLOW_MARKER,
    DESKTOP_APP_RULES,
    EXEMPT_FILES,
    audit,
    audit_repository,
)

REPO_ROOT = Path(__file__).parent.parent
PACKAGE_ROOT = REPO_ROOT / "desktop_app"

# Everything the GUI may import. Deliberately absent: anything that reaches a
# network (socket, urllib, http), starts a process (subprocess, os.system) or
# signs a request (hmac, hashlib for keys). Adding a name here is a decision,
# not a formality.
ALLOWED_ROOTS = {
    "desktop_app",
    "crypto_research",
    "__future__",
    "collections",
    "csv",
    "dataclasses",
    "decimal",
    "enum",
    "json",
    "logging",
    "pathlib",
    "queue",
    "re",
    "sys",
    "threading",
    "tkinter",
    "typing",
}


def _python_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


class SafetyZoneTests(unittest.TestCase):
    def test_desktop_app_is_covered_by_the_repository_audit(self) -> None:
        report = audit_repository(REPO_ROOT)
        self.assertIn("desktop-app", report.zones)
        self.assertTrue(report.ok, report.summary())

    def test_desktop_app_alone_is_free_of_signing_and_credentials(self) -> None:
        report = audit(PACKAGE_ROOT, rules=DESKTOP_APP_RULES)
        self.assertTrue(report.ok, report.summary())
        self.assertGreater(report.files_scanned, 10)

    def test_no_file_is_named_so_that_the_audit_would_skip_it(self) -> None:
        # The audit exempts files by name (safety.py, which has to spell out
        # what it forbids). Nothing in desktop_app may borrow that exemption.
        for path in _python_files():
            self.assertNotIn(path.name, EXEMPT_FILES, f"{path} would be skipped by the audit")

    def test_the_opt_out_marker_is_only_used_for_tk_widget_delete(self) -> None:
        # Tkinter's Listbox/Text clear themselves with .delete(), which the audit's
        # HTTP-DELETE pattern cannot tell apart. That is the only reason the
        # marker is used here; anything wider would quietly weaken the audit.
        for path in _python_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if ALLOW_MARKER in line:
                    self.assertIn(".delete(", line, f"{path}:{number} uses the opt-out marker")

    def test_no_module_imports_account_tools(self) -> None:
        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    self.assertNotEqual(
                        name.split(".")[0], "account_tools", f"{path} imports account_tools"
                    )

    def test_imports_stay_inside_an_explicit_allow_list(self) -> None:
        found: set[str] = set()
        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
        self.assertEqual(found - ALLOWED_ROOTS, set())

    def test_nothing_accepts_or_even_mentions_a_credential(self) -> None:
        # The GUI reads prices and runs practice backtests. It has no field that
        # accepts a key or secret, and no code that mentions one.
        forbidden_words = ("api_key", "api_secret", "apikey", "secret_key", "password")
        for path in _python_files():
            text = path.read_text(encoding="utf-8").lower()
            for word in forbidden_words:
                self.assertNotIn(word, text, f"{path} mentions {word!r}")


if __name__ == "__main__":
    unittest.main()
