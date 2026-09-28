"""desktop_app.errors -- plain-language translation, never a raw traceback."""

from __future__ import annotations

import unittest

from crypto_research.errors import ConfigError

from desktop_app import errors


class ExplainTests(unittest.TestCase):
    def test_known_fragment_is_translated_and_hides_technical_wording(self) -> None:
        exc = ConfigError(
            "no decision times fall inside the run window; check start_time, "
            "end_time and the downloaded bar range"
        )
        text = errors.explain(exc)
        self.assertNotIn("start_time", text)
        self.assertIn("dates", text.lower())

    def test_the_messages_the_sweep_itself_raises_are_translated(self) -> None:
        # The exact wording sweep.py raises when the local store is incomplete.
        missing_snapshots = ConfigError(
            "no exchangeInfo snapshots in data/market.sqlite; run "
            "`python -m crypto_research data refresh-rules` first"
        )
        missing_rules = ConfigError(
            "no Binance.US trading rules stored for ZECUSDT; download them before running"
        )
        for exc in (missing_snapshots, missing_rules):
            with self.subTest(message=str(exc)[:40]):
                text = errors.explain(exc)
                self.assertNotIn("python -m", text)
                self.assertNotIn("exchangeInfo", text)

    def test_missing_bars_message_is_translated(self) -> None:
        text = errors.explain(
            ConfigError(
                "no 1d bars stored for ZECUSDT between 2020-01-01 and 2020-02-01; "
                "download them first"
            )
        )
        self.assertIn("price history", text.lower())

    def test_a_data_gap_message_is_translated(self) -> None:
        text = errors.explain(
            ConfigError("market data has 1 gap(s) wider than 3 1d bar(s); the largest is ...")
        )
        self.assertIn("gap", text.lower())
        self.assertNotIn("bar(s)", text)

    def test_unknown_crypto_research_error_is_still_readable(self) -> None:
        text = errors.explain(ConfigError("[data] is missing 'symbols'"))
        self.assertIn("couldn't be set up", text)

    def test_generic_exception_never_shows_a_traceback_or_file_path(self) -> None:
        text = errors.explain(ValueError("boom"))
        self.assertNotIn("Traceback", text)
        self.assertNotIn(".py", text)
        self.assertIn("practice math", text)


if __name__ == "__main__":
    unittest.main()
