"""desktop_app.report_loader -- Markdown/plain-text classification and the catalog."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from desktop_app import report_loader
from desktop_app.report_loader import Block

REPO_ROOT = Path(__file__).parent.parent


def kinds(text: str) -> list[str]:
    return [block.kind for block in report_loader.parse(text)]


class InlineTests(unittest.TestCase):
    def _only(self, text: str) -> Block:
        (block,) = report_loader.parse(text)
        return block

    def test_bold_italic_and_code_are_styled_and_their_markers_removed(self) -> None:
        block = self._only("plain **bold** and *italic* and `code` end")
        self.assertEqual(block.text, "plain bold and italic and code end")
        styled = {style: block.text[start:end] for start, end, style in block.spans}
        self.assertEqual(styled, {"bold": "bold", "italic": "italic", "code": "code"})

    def test_a_link_keeps_its_words_and_drops_the_address(self) -> None:
        block = self._only("see [the report](../results/x.md) for more")
        self.assertEqual(block.text, "see the report for more")
        self.assertEqual(block.spans, ())

    def test_an_asterisk_that_is_not_emphasis_is_left_alone(self) -> None:
        block = self._only("5 * 3 = 15")
        self.assertEqual(block.text, "5 * 3 = 15")

    def test_offsets_stay_correct_after_earlier_markers_are_removed(self) -> None:
        block = self._only("**a** then `b` then *c*")
        self.assertEqual(block.text, "a then b then c")
        self.assertEqual([block.text[s:e] for s, e, _ in block.spans], ["a", "b", "c"])


class ParseTests(unittest.TestCase):
    def test_headers_at_every_depth(self) -> None:
        blocks = report_loader.parse("# Big\n## Medium\n### Small\n#### Smaller\n")
        self.assertEqual([b.kind for b in blocks], ["h1", "h2", "h3", "h3"])
        self.assertEqual(blocks[0].text, "Big")

    def test_bullets_take_any_marker_and_lose_it(self) -> None:
        blocks = report_loader.parse("- one\n* two\n+ three\n")
        self.assertEqual([b.kind for b in blocks], ["bullet"] * 3)
        self.assertEqual([b.text for b in blocks], ["one", "two", "three"])

    def test_numbered_lines_keep_their_number(self) -> None:
        (block,) = report_loader.parse("2. second thing\n")
        self.assertEqual((block.kind, block.text), ("numbered", "2. second thing"))

    def test_a_table_becomes_one_block_with_the_separator_row_dropped(self) -> None:
        text = "| Held | Result |\n|---|---|\n| 1 year | **$220** |\n| 2 years | $254 |\n"
        (block,) = report_loader.parse(text)
        self.assertEqual(block.kind, "table")
        self.assertEqual(
            block.cells,
            (("Held", "Result"), ("1 year", "$220"), ("2 years", "$254")),
        )

    def test_a_table_with_alignment_colons_is_recognised(self) -> None:
        (block,) = report_loader.parse("| a | b |\n|:---|---:|\n| 1 | 2 |\n")
        self.assertEqual(block.cells, (("a", "b"), ("1", "2")))

    def test_two_tables_stay_two_tables(self) -> None:
        text = "| a |\n|---|\n| 1 |\n\ntext between\n\n| b |\n|---|\n| 2 |\n"
        self.assertEqual(kinds(text), ["table", "blank", "body", "blank", "table"])

    def test_a_dash_alone_in_a_data_cell_is_not_mistaken_for_a_separator(self) -> None:
        (block,) = report_loader.parse("| a | b |\n|---|---|\n| - | 1 |\n")
        self.assertEqual(block.cells, (("a", "b"), ("-", "1")))

    def test_blank_and_rule_lines(self) -> None:
        self.assertEqual(kinds("\n---\n"), ["blank", "rule"])

    def test_everything_else_is_plain_body_text(self) -> None:
        (block,) = report_loader.parse("just a sentence.\n")
        self.assertEqual((block.kind, block.text), ("body", "just a sentence."))


class ParagraphJoiningTests(unittest.TestCase):
    def test_a_paragraph_wrapped_at_the_margin_is_rejoined(self) -> None:
        text = (
            "Every date was checked against sources by web search, plus two other\n"
            "significant factual claims stated in conversation but never\n"
            "explicitly sourced at the time.\n"
        )
        (block,) = report_loader.parse(text)
        self.assertEqual(block.kind, "body")
        self.assertNotIn("\n", block.text)
        self.assertTrue(block.text.startswith("Every date was checked"))
        self.assertTrue(block.text.endswith("sourced at the time."))

    def test_short_consecutive_lines_are_left_as_separate_lines(self) -> None:
        # A title block, as in FINAL_REPORT.md: three lines meant to stay three lines.
        text = (
            "**Prepared:** 27 September 2026\n"
            "**Covers:** every test run in this project, from the first benchmark to the last.\n"
            "**Written for:** someone with no background in trading.\n"
        )
        self.assertEqual(kinds(text), ["body", "body", "body"])

    def test_a_long_line_that_ends_a_sentence_starts_a_new_line(self) -> None:
        text = (
            "This whole sentence is long enough to look like it was wrapped at a margin.\n"
            "But it ended properly, so this line is a new one.\n"
        )
        self.assertEqual(kinds(text), ["body", "body"])

    def test_an_indented_continuation_joins_its_bullet(self) -> None:
        (block,) = report_loader.parse("- the first part of an item\n  and the rest of it\n")
        self.assertEqual((block.kind, block.text), ("bullet", "the first part of an item and the rest of it"))

    def test_a_bullet_after_a_paragraph_starts_a_new_block(self) -> None:
        self.assertEqual(kinds("some text that ends here.\n- an item\n"), ["body", "bullet"])

    def test_formatting_survives_joining(self) -> None:
        text = (
            "This is a long opening line that runs up to the margin and has **bold\n"
            "across the wrap** in it.\n"
        )
        (block,) = report_loader.parse(text)
        self.assertNotIn("**", block.text)
        self.assertEqual([block.text[s:e] for s, e, _ in block.spans], ["bold across the wrap"])


class QuoteAndCodeTests(unittest.TestCase):
    def test_a_quote_loses_its_marker_and_keeps_its_formatting(self) -> None:
        (block,) = report_loader.parse("> **What this is.** A study of *what happened*.\n")
        self.assertEqual(block.kind, "quote")
        self.assertEqual(block.text, "What this is. A study of what happened.")
        self.assertEqual(
            [block.text[s:e] for s, e, _ in block.spans], ["What this is.", "what happened"]
        )

    def test_a_bare_marker_separates_quoted_paragraphs(self) -> None:
        blocks = report_loader.parse("> first point\n>\n> second point\n")
        self.assertEqual(
            [(b.kind, b.text) for b in blocks], [("quote", "first point"), ("quote", "second point")]
        )

    def test_a_quote_wrapped_at_the_margin_is_rejoined(self) -> None:
        text = (
            "> A quoted sentence that is long enough to have been wrapped at the margin and\n"
            "> carries on here.\n"
        )
        (block,) = report_loader.parse(text)
        self.assertEqual(block.kind, "quote")
        self.assertNotIn("\n", block.text)
        self.assertTrue(block.text.endswith("carries on here."))

    def test_text_after_a_quote_is_a_separate_paragraph(self) -> None:
        self.assertEqual(kinds("> quoted\nplain text after\n"), ["quote", "body"])

    def test_a_code_fence_is_kept_verbatim_and_not_interpreted(self) -> None:
        text = (
            "before\n```\npython scripts/x.py\n  - not a bullet **nor bold**\n"
            "| not | a table |\n```\nafter\n"
        )
        blocks = report_loader.parse(text)
        self.assertEqual([b.kind for b in blocks], ["body", "code", "code", "code", "body"])
        self.assertEqual(blocks[2].text, "  - not a bullet **nor bold**")
        self.assertEqual(blocks[3].text, "| not | a table |")

    def test_a_language_tag_on_the_fence_is_accepted(self) -> None:
        self.assertEqual(kinds("```bash\nls\n```\n"), ["code"])


class ParsePlainTests(unittest.TestCase):
    def test_every_line_is_kept_exactly_as_written(self) -> None:
        text = "1. LEVERAGE COST (funding)\n  - not a bullet\n| a | b |\n**not bold**\n"
        blocks = report_loader.parse_plain(text)
        self.assertEqual([b.kind for b in blocks], ["body"] * 4)
        self.assertEqual(
            [b.text for b in blocks],
            ["1. LEVERAGE COST (funding)", "  - not a bullet", "| a | b |", "**not bold**"],
        )

    def test_blank_lines_stay_blank_and_alignment_is_preserved(self) -> None:
        blocks = report_loader.parse_plain("asset      1 in 20\n\nBitcoin        82\n")
        self.assertEqual([b.kind for b in blocks], ["body", "blank", "body"])
        self.assertEqual(blocks[0].text, "asset      1 in 20")


class CatalogTests(unittest.TestCase):
    def test_every_entry_offered_in_this_repo_loads_readable_content(self) -> None:
        entries = report_loader.catalog(REPO_ROOT)
        self.assertGreater(len(entries), 0)
        for entry in entries:
            with self.subTest(entry=entry.key):
                text = "".join(
                    block.text + "".join(cell for row in block.cells for cell in row)
                    for block in report_loader.load(REPO_ROOT, entry)
                )
                self.assertTrue(text.strip(), f"{entry.path} loaded but rendered blank")

    def test_no_markdown_syntax_is_left_showing_in_any_offered_document(self) -> None:
        # These files are edited by others while the app exists. If a construct
        # appears that the loader doesn't understand, it would reach the screen
        # as stray symbols; this catches that here instead.
        for entry in report_loader.catalog(REPO_ROOT):
            if not entry.is_markdown:
                continue
            for number, block in enumerate(report_loader.load(REPO_ROOT, entry), 1):
                with self.subTest(entry=entry.key, block=number):
                    texts = [block.text] if block.kind != "table" else [c for row in block.cells for c in row]
                    if block.kind == "code":
                        continue
                    for text in texts:
                        self.assertNotIn("**", text)
                        self.assertNotIn("](", text)
                        # A heading, quote, list or table marker still at the start of a
                        # line. ("#1" with no space is a rank, not a heading.)
                        self.assertIsNone(
                            re.match(r"^(#{1,6}\s|>|[-*+]\s|\|)", text), text[:60]
                        )

    def test_the_ai_handoff_document_is_never_offered(self) -> None:
        # CONTEXT_FOR_CLAUDE.md briefs an AI and describes the reader in the
        # third person. It must not be shown to the person it talks about.
        offered = {entry.path for entry in report_loader.catalog(REPO_ROOT)}
        self.assertNotIn("CONTEXT_FOR_CLAUDE.md", offered)
        self.assertNotIn("CONTEXT_FOR_CLAUDE.md", {entry.path for entry in report_loader.CATALOG})
        self.assertTrue(all(path.startswith("results/") for path in offered))

    def test_the_plain_language_final_report_leads_when_present(self) -> None:
        entries = report_loader.catalog(REPO_ROOT)
        if (REPO_ROOT / "results" / "FINAL_REPORT.md").exists():
            self.assertEqual(entries[0].path, "results/FINAL_REPORT.md")

    def test_curated_keys_and_titles_are_unique(self) -> None:
        keys = [entry.key for entry in report_loader.CATALOG]
        titles = [entry.title for entry in report_loader.CATALOG]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(titles), len(set(titles)))

    def test_markdown_is_told_apart_from_plain_text(self) -> None:
        by_path = {entry.path: entry for entry in report_loader.CATALOG}
        self.assertTrue(by_path["results/FINAL_REPORT.md"].is_markdown)
        self.assertFalse(by_path["results/scenarios_report.txt"].is_markdown)


class CatalogDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "results").mkdir()

    def _touch(self, relative: str, text: str = "content\n") -> None:
        (self.root / relative).write_text(text, encoding="utf-8")

    def test_a_missing_repo_yields_an_empty_catalog_not_a_crash(self) -> None:
        self.assertEqual(report_loader.catalog(Path("Z:/definitely/not/a/real/repo")), [])

    def test_a_curated_entry_whose_file_is_gone_is_dropped(self) -> None:
        self._touch("results/FINAL_REPORT.md")
        keys = [entry.key for entry in report_loader.catalog(self.root)]
        self.assertEqual(keys, ["final_report"])

    def test_a_new_report_is_picked_up_with_a_readable_title(self) -> None:
        self._touch("results/tax_effects_report.txt")
        (entry,) = report_loader.catalog(self.root)
        self.assertEqual(entry.title, "Tax effects")
        self.assertEqual(entry.path, "results/tax_effects_report.txt")
        self.assertFalse(entry.is_markdown)

    def test_a_curated_report_is_not_listed_twice(self) -> None:
        self._touch("results/valuation_report.txt")
        paths = [entry.path for entry in report_loader.catalog(self.root)]
        self.assertEqual(paths, ["results/valuation_report.txt"])

    def test_raw_data_and_unrelated_files_are_not_offered(self) -> None:
        for name in ("risk_profile.csv", "usdt_universe.txt", "notes.md", "CONTEXT_FOR_CLAUDE.md"):
            self._touch(f"results/{name}")
        self._touch("CONTEXT_FOR_CLAUDE.md")
        self.assertEqual(report_loader.catalog(self.root), [])

    def test_curated_entries_come_before_discovered_ones(self) -> None:
        self._touch("results/zzz_new_report.txt")
        self._touch("results/FINAL_REPORT.md")
        keys = [entry.key for entry in report_loader.catalog(self.root)]
        self.assertEqual(keys, ["final_report", "zzz_new_report"])


if __name__ == "__main__":
    unittest.main()
