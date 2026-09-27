"""Experiment configuration parsing."""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from crypto_research.backtest.costs import CommissionAssetPolicy
from crypto_research.backtest.execution import (
    FixedFractionFillModel,
    FullFillModel,
    VolumeCapFillModel,
)
from crypto_research.config import load_config, parse_config
from crypto_research.decimals import dec
from crypto_research.errors import ConfigError
from crypto_research.models import CashSource, ContributionType
from crypto_research.timeutil import parse_time

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"

MINIMAL = """
[experiment]
name = "t"

[data]
symbols = ["BTCUSD"]
interval = "1d"
start = "2024-01-01T00:00:00Z"
end = "2024-03-01T00:00:00Z"

[strategy]
name = "benchmark0_hold"
"""


def parse(text: str):
    return parse_config(tomllib.loads(text), source_path="<test>")


class MinimalConfigTests(unittest.TestCase):
    def test_parses_and_applies_defaults(self) -> None:
        config = parse(MINIMAL)
        self.assertEqual(config.name, "t")
        self.assertEqual(config.data.symbols, ("BTCUSD",))
        self.assertEqual(config.data.interval, "1d")
        self.assertEqual(config.backtest.valuation_asset, "USD")
        self.assertEqual(config.backtest.initial_cash, {})
        self.assertEqual(config.backtest.starting_holdings, ())
        self.assertIsInstance(config.fill_model, FullFillModel)

    def test_times_are_parsed_to_epoch_ms(self) -> None:
        config = parse(MINIMAL)
        self.assertEqual(config.data.start_time, parse_time("2024-01-01T00:00:00Z"))
        self.assertLess(config.data.start_time, config.data.end_time)

    def test_missing_data_section_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            parse('[experiment]\nname = "t"\n[strategy]\nname = "benchmark0_hold"\n')

    def test_missing_strategy_section_is_refused(self) -> None:
        text = MINIMAL.replace('[strategy]\nname = "benchmark0_hold"\n', "")
        with self.assertRaises(ConfigError):
            parse(text)

    def test_an_interval_binance_us_does_not_offer_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            parse(MINIMAL.replace('interval = "1d"', 'interval = "7m"'))

    def test_a_backwards_date_range_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            parse(MINIMAL.replace('end = "2024-03-01T00:00:00Z"', 'end = "2023-01-01T00:00:00Z"'))

    def test_symbols_are_upper_cased(self) -> None:
        config = parse(MINIMAL.replace('["BTCUSD"]', '["btcusd"]'))
        self.assertEqual(config.data.symbols, ("BTCUSD",))


class PortfolioSectionTests(unittest.TestCase):
    TEXT = MINIMAL + """
[portfolio]
valuation_asset = "USD"
initial_cash = { USD = "200.00" }
assumed_rates = { "USDT->USD" = "1.0" }

[[portfolio.starting_holdings]]
asset = "BTC"
quantity = "0.0021"
cost_basis_per_unit = "62000"
cost_basis_asset = "USD"

[[portfolio.starting_holdings]]
asset = "BNB"
quantity = "0.31"

[[portfolio.external_contributions]]
amount = "25"
asset = "USD"
availability_time = "2024-02-01T00:00:00Z"
contribution_type = "RECURRING_DEPOSIT"
label = "payday"
"""

    def setUp(self) -> None:
        self.config = parse(self.TEXT)

    def test_initial_cash_is_a_decimal_per_asset(self) -> None:
        self.assertEqual(self.config.backtest.initial_cash, {"USD": dec("200.00")})

    def test_starting_holdings_are_parsed_and_stay_non_cash(self) -> None:
        holdings = {h.asset: h for h in self.config.backtest.starting_holdings}
        self.assertEqual(holdings["BTC"].quantity, dec("0.0021"))
        self.assertEqual(holdings["BTC"].cost_basis_per_unit, dec("62000"))
        self.assertEqual(holdings["BTC"].cost_basis_asset, "USD")
        self.assertIsNone(holdings["BNB"].cost_basis_per_unit)

    def test_contributions_are_parsed_with_their_availability_time(self) -> None:
        contribution = self.config.backtest.external_contributions[0]
        self.assertEqual(contribution.amount, dec("25"))
        self.assertEqual(contribution.contribution_type, ContributionType.RECURRING_DEPOSIT)
        self.assertEqual(
            contribution.availability_time, parse_time("2024-02-01T00:00:00Z")
        )

    def test_assumed_rates_use_an_explicit_pair_form(self) -> None:
        self.assertEqual(
            self.config.backtest.assumed_rates, {("USDT", "USD"): dec("1.0")}
        )

    def test_a_bare_asset_assumed_rate_is_refused(self) -> None:
        # Writing `USDT = 1` would be asserting a stablecoin is a dollar
        # without saying so.
        with self.assertRaises(ConfigError):
            parse(MINIMAL + '\n[portfolio]\nassumed_rates = { USDT = "1.0" }\n')

    def test_a_lower_case_cash_asset_is_normalized(self) -> None:
        config = parse(MINIMAL + '\n[portfolio]\ninitial_cash = { usd = "10" }\n')
        self.assertEqual(config.backtest.initial_cash, {"USD": dec("10")})


class CostSectionTests(unittest.TestCase):
    TEXT = MINIMAL + """
[costs.fees]
maker_rate = "0.0015"
taker_rate = "0.0025"
source = "Binance.US fee schedule, tier 0, checked 2026-09-26"
commission_asset_policy = "QUOTE_ASSET"

[costs.fees.per_symbol.BTCUSD]
maker_rate = "0"
taker_rate = "0"
source = "zero-fee promotion"

[costs.spread]
default_bps = "8"
prefer_observed = false

[costs.slippage]
model = "participation"
base_bps = "3"
coefficient_bps = "40"

[costs.latency]
decision_to_submit_ms = 750
"""

    def setUp(self) -> None:
        self.costs = parse(self.TEXT).backtest.costs

    def test_fee_rates_and_provenance(self) -> None:
        described = self.costs.fees.describe()
        self.assertEqual(described["default"]["maker_rate"], "0.0015")
        self.assertEqual(described["default"]["taker_rate"], "0.0025")
        self.assertIn("2026-09-26", described["default"]["source"])

    def test_per_symbol_fee_override(self) -> None:
        described = self.costs.fees.describe()
        self.assertEqual(described["per_symbol"]["BTCUSD"]["taker_rate"], "0")

    def test_commission_asset_policy(self) -> None:
        self.assertEqual(
            self.costs.fees.commission_asset_policy, CommissionAssetPolicy.QUOTE_ASSET
        )

    def test_spread_and_slippage_models(self) -> None:
        self.assertEqual(self.costs.spread.describe()["default_bps"], "8")
        self.assertFalse(self.costs.spread.describe()["prefer_observed"])
        self.assertEqual(
            self.costs.slippage.describe()["model"], "ParticipationSlippageModel"
        )

    def test_latency(self) -> None:
        self.assertEqual(self.costs.latency.decision_to_submit_ms, 750)

    def test_every_default_cost_is_labelled_as_an_assumption(self) -> None:
        costs = parse(MINIMAL).backtest.costs
        self.assertEqual(costs.fees.describe()["default"]["source"], "ASSUMPTION")
        self.assertIn("ASSUMPTION", costs.spread.describe()["note"])
        self.assertIn("ASSUMPTION", costs.slippage.describe()["note"])
        self.assertIn("ASSUMPTION", costs.latency.describe()["note"])

    def test_an_unknown_slippage_model_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            parse(MINIMAL + '\n[costs.slippage]\nmodel = "magic"\n')

    def test_an_impossible_fee_rate_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            parse(MINIMAL + '\n[costs.fees]\ntaker_rate = "1.5"\n')


class ExecutionSectionTests(unittest.TestCase):
    def test_selling_cannot_be_switched_on_from_a_config(self) -> None:
        with self.assertRaises(ConfigError) as ctx:
            parse(MINIMAL + "\n[execution]\nallow_sell = true\n")
        self.assertIn("V1 does not model selling", str(ctx.exception))

    def test_allow_sell_is_false_by_default(self) -> None:
        self.assertFalse(parse(MINIMAL).backtest.execution.allow_sell)

    def test_staleness_window_is_configurable(self) -> None:
        config = parse(MINIMAL + "\n[execution]\nmax_reference_age_ms = 30000\n")
        self.assertEqual(config.backtest.execution.max_reference_age_ms, 30_000)

    def test_default_funding_source(self) -> None:
        config = parse(
            MINIMAL + '\n[execution]\ndefault_funding_source = "INITIAL"\n'
        )
        self.assertEqual(
            config.backtest.execution.default_funding_source, CashSource.INITIAL
        )


class FillModelSectionTests(unittest.TestCase):
    def test_fixed_fraction(self) -> None:
        config = parse(
            MINIMAL
            + '\n[fill]\nmodel = "fixed_fraction"\nfractions = ["0.5", "0.25"]\n'
        )
        self.assertIsInstance(config.fill_model, FixedFractionFillModel)
        plan = config.fill_model.fractions
        self.assertEqual(list(plan), [dec("0.5"), dec("0.25")])

    def test_volume_cap(self) -> None:
        config = parse(
            MINIMAL + '\n[fill]\nmodel = "volume_cap"\nmax_participation = "0.02"\n'
        )
        self.assertIsInstance(config.fill_model, VolumeCapFillModel)
        self.assertEqual(config.fill_model.max_participation, dec("0.02"))

    def test_an_unknown_fill_model_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            parse(MINIMAL + '\n[fill]\nmodel = "psychic"\n')


class ShippedConfigTests(unittest.TestCase):
    def test_every_bundled_config_parses(self) -> None:
        configs = sorted(CONFIG_DIR.glob("*.toml"))
        self.assertTrue(configs, "no example configs found")
        for path in configs:
            with self.subTest(config=path.name):
                config = load_config(path)
                self.assertTrue(config.name)
                self.assertTrue(config.data.symbols)

    def test_bundled_configs_do_not_hard_code_a_final_asset_trio(self) -> None:
        # Candidate assets come from configuration; nothing in the code picks
        # them. The example configs are explicitly labelled as candidates.
        for path in sorted(CONFIG_DIR.glob("*.toml")):
            text = path.read_text(encoding="utf-8").lower()
            with self.subTest(config=path.name):
                self.assertIn("candidate", text)

    def test_a_missing_config_file_is_reported_clearly(self) -> None:
        with self.assertRaises(ConfigError):
            load_config(CONFIG_DIR / "does_not_exist.toml")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
