"""Research modules must survive installation outside the source checkout."""

import fnmatch
from pathlib import Path
import tomllib
import unittest


class IntelligencePackagingTests(unittest.TestCase):
    def test_all_intelligence_packages_are_included_in_distribution_discovery(self):
        root = Path(__file__).resolve().parent.parent
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = config["tool"]["setuptools"]["packages"]["find"]["include"]
        packages = [
            ".".join(path.parent.relative_to(root).parts)
            for path in (root / "external_intelligence").rglob("__init__.py")
        ]
        self.assertIn("external_intelligence.research", packages)
        self.assertIn("external_intelligence.whale_alert", packages)
        for package in packages:
            with self.subTest(package=package):
                self.assertTrue(any(fnmatch.fnmatchcase(package, p) for p in patterns))

    def test_distribution_keeps_the_existing_engine_and_account_packages(self):
        root = Path(__file__).resolve().parent.parent
        config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        patterns = config["tool"]["setuptools"]["packages"]["find"]["include"]
        for package in ("crypto_research", "crypto_research.backtest", "account_tools"):
            self.assertTrue(any(fnmatch.fnmatchcase(package, p) for p in patterns))
        self.assertEqual(config["project"]["dependencies"], [])
