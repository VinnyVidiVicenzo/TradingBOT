"""Every term the app actually hovers over must resolve to a real definition."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from desktop_app import coin_catalog, glossary, metrics_presenter, plan_builder, sweep
from desktop_app.app import SCREENS
from desktop_app.widgets import start_map

PACKAGE = Path(__file__).parent.parent / "desktop_app"
_FALLBACK = glossary.define("__not_a_real_term__")


def _is_defined(term: str) -> bool:
    return glossary.define(term) != _FALLBACK


def _literal_terms_passed_to_attach_tooltip() -> list[tuple[str, int, str]]:
    """(file, line, term) for every attach_tooltip(widget, "literal") call in the app."""
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            term = node.args[1]
            if name == "attach_tooltip" and isinstance(term, ast.Constant) and isinstance(term.value, str):
                found.append((path.name, node.lineno, term.value))
    return found


class NoDanglingReferenceTests(unittest.TestCase):
    def test_every_metrics_presenter_term_resolves(self) -> None:
        for term in metrics_presenter.GLOSSARY_TERMS_USED:
            with self.subTest(term=term):
                self.assertTrue(_is_defined(term))

    def test_every_schedule_label_resolves(self) -> None:
        for label in plan_builder.SCHEDULE_LABELS.values():
            with self.subTest(label=label):
                self.assertTrue(_is_defined(label.lower()))

    def test_every_hold_length_resolves(self) -> None:
        for label in sweep.HOLD_LABELS.values():
            with self.subTest(label=label):
                self.assertTrue(_is_defined(label.lower()))

    def test_every_coin_that_can_be_offered_resolves(self) -> None:
        for name in coin_catalog.FRIENDLY_NAMES.values():
            with self.subTest(coin=name):
                self.assertTrue(_is_defined(name.lower()))

    def test_every_screen_button_resolves(self) -> None:
        for _key, label in SCREENS:
            with self.subTest(screen=label):
                self.assertTrue(_is_defined(label.lower()))

    def test_every_map_legend_entry_resolves(self) -> None:
        for _bucket, words in start_map.LEGEND:
            with self.subTest(legend=words):
                self.assertTrue(_is_defined(words))

    def test_every_literal_term_written_in_the_code_resolves(self) -> None:
        # Catches a typo in any attach_tooltip(widget, "some term") written out
        # literally anywhere in the app. Most calls pass a variable instead
        # (a label, a coin name) and are covered by the explicit lists above.
        calls = _literal_terms_passed_to_attach_tooltip()
        self.assertGreaterEqual(len(calls), 3)  # the scan itself must be finding things
        for filename, line, term in calls:
            with self.subTest(where=f"{filename}:{line}", term=term):
                self.assertTrue(_is_defined(term))

    def test_the_form_labels_written_in_the_plan_screen_all_have_explanations(self) -> None:
        for label in ("How much money?", "Which coins?", "How should it go in?", "How long would you hold it?"):
            with self.subTest(label=label):
                self.assertTrue(_is_defined(label.lower()))

    def test_lookup_is_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual(glossary.define("WORST STARTING MONTH"), glossary.define(" worst starting month "))

    def test_unknown_term_gets_the_visible_fallback_not_a_key_error(self) -> None:
        self.assertIn("no plain-English definition", _FALLBACK)

    def test_no_definition_is_blank_or_an_essay(self) -> None:
        # A hover explanation is meant to be brief.
        for term, text in glossary.DEFINITIONS.items():
            with self.subTest(term=term):
                self.assertTrue(text.strip())
                self.assertLessEqual(len(text), 320, "too long for a hover explanation")


if __name__ == "__main__":
    unittest.main()
