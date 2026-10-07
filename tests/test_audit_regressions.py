"""Regressions for audit findings, using synthetic inputs and no live services."""

from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from dataclasses import replace
from decimal import Decimal
from email.message import Message
from pathlib import Path

from crypto_research.backtest.costs import CostModels, LatencyModel
from crypto_research.backtest.engine import BacktestConfig, Backtester
from crypto_research.backtest.metrics import compute_metrics
from crypto_research.backtest.portfolio import RateView
from crypto_research.datafeed.dataset import MarketDataSet
from crypto_research.decimals import dec, format_decimal
from crypto_research.errors import ConfigError
from crypto_research.experiments.registry import _write_csv
from crypto_research.http_policy import no_redirect_opener, pagination_url
from crypto_research.models import ExternalContribution, PortfolioHolding
from crypto_research.timeutil import DAY_MS
from desktop_app import alternatives, plan_history, sweep_summary
from desktop_app.widgets.sweep_result import SweepResultView
from external_intelligence.base.clock import FixedClock
from external_intelligence.base.coverage import CoverageIndex, CoverageInterval
from external_intelligence.base.formats import parse_records, UnparseableRecord
from external_intelligence.base.health import ProviderHealth
from external_intelligence.base.receipt import ReceiptPolicy
from external_intelligence.base.transport import FetchRequest, FetchResponse, UrllibGetFetcher, fetch_json
from external_intelligence.binanceus_trades import PollTarget, poll
from external_intelligence.errors import ProviderError, ProviderSchemaError
from external_intelligence.ingest import ingest_text
from external_intelligence.coinlobster import CoinLobsterAdapter
from external_intelligence.storage import IntelligenceStore
from paper_trading import ticklog
from .helpers import EPOCH_2024, default_rule_book, make_bars, make_quote
from .test_engine import RecordingStrategy
from .test_desktop_app_alternatives import reference, result, START, BASE
from .test_external_intelligence_binanceus_trades import Harness, raw, T0, FakeFetcher


class AccountingAuditTests(unittest.TestCase):
    def run_case(self, *, delay=500, end_offset=1000, holdings=(), contribution=False):
        bars = make_bars("BTCUSD", ["100"])
        config = BacktestConfig(
            start_time=EPOCH_2024, end_time=bars[-1].close_time + end_offset,
            initial_cash={"USD": dec("200")}, starting_holdings=holdings,
            external_contributions=(ExternalContribution(dec("50"), "USD", EPOCH_2024),) if contribution else (),
            costs=replace(CostModels(), latency=LatencyModel(delay)),
        )
        return Backtester(dataset=MarketDataSet({"BTCUSD": bars}),
                          rule_book=default_rule_book(), config=config).run(RecordingStrategy(buy_quote="200"))

    def test_last_fill_costs_and_cash_match_final_portfolio(self):
        run = self.run_case()
        metrics = compute_metrics(run)
        rates = RateView(run.final_prices, valuation_asset="USD")
        self.assertEqual(metrics.ending_portfolio_value, run.portfolio.value(rates))
        self.assertEqual(metrics.uninvested_cash_value, run.portfolio.cash_value(rates))
        self.assertLess(metrics.net_profit, 0)
        self.assertEqual(metrics.last_mark_time, run.config.end_time)

    def test_fill_after_end_is_explicitly_rejected_without_spending(self):
        run = self.run_case(end_offset=1)
        self.assertEqual(run.orders[0].rejection_reason.value, "OUTSIDE_RUN_WINDOW")
        self.assertEqual(run.portfolio.cash.available("USD"), dec("200"))
        self.assertEqual(compute_metrics(run).ending_portfolio_value, dec("200"))

    def test_zero_latency_preserves_initial_capital_and_cost_loss(self):
        metrics = compute_metrics(self.run_case(delay=0))
        self.assertEqual(metrics.total_capital_in, dec("200"))
        self.assertLess(metrics.net_return, 0)
        self.assertLess(metrics.time_weighted_return, 0)
        self.assertGreater(metrics.max_drawdown, 0)

    def test_early_deposit_is_counted_once_and_does_not_hide_costs(self):
        metrics = compute_metrics(self.run_case(delay=0, contribution=True))
        self.assertEqual(metrics.total_capital_in, dec("250"))
        self.assertEqual(metrics.total_external_contributions, dec("50"))
        self.assertLess(metrics.net_profit, 0)

    def test_unpriceable_holdings_cannot_complete_with_zero_metrics(self):
        with self.assertRaises(ConfigError):
            self.run_case(holdings=(PortfolioHolding("FOO", dec("1")),))

    def test_quote_changes_change_dataset_identity(self):
        bars = make_bars("BTCUSD", ["100"])
        a = make_quote("BTCUSD", "99", "101", received_at=bars[0].close_time)
        b = replace(a, ask=dec("102"))
        self.assertNotEqual(MarketDataSet({"BTCUSD": bars}, {"BTCUSD": [a]}).version.sha256,
                            MarketDataSet({"BTCUSD": bars}, {"BTCUSD": [b]}).version.sha256)


class ComparisonAuditTests(unittest.TestCase):
    def test_all_columns_use_the_same_available_starting_months(self):
        early = BASE - 40 * DAY_MS
        plan = result(starts=(early, START))
        plan = replace(plan, outcomes=(replace(plan.outcomes[0], ending_value=dec("10000")), plan.outcomes[1]))
        comparison, count = SweepResultView._compare(plan, sweep_summary.summarize(plan), reference())
        self.assertEqual(count, 1)
        self.assertEqual(comparison.rows[1].cells[0].text, "$200")
        self.assertEqual(comparison.rows[3].cells[0].text, "0 of 1")

    def test_large_internal_reference_gap_is_not_covered(self):
        prices = {BASE + i * DAY_MS: 100.0 for i in range(1500) if not 150 <= i < 330}
        yields = {BASE + i * DAY_MS: 4.0 for i in range(1500)}
        ref = alternatives.build_reference(prices, yields)
        self.assertIsNone(alternatives.compare(result(), ref))

    def test_invalid_fund_prices_disable_comparison(self):
        for value in (0.0, float("nan"), float("inf"), -1.0):
            prices = {BASE + i * DAY_MS: value for i in range(100)}
            yields = {BASE + i * DAY_MS: 4.0 for i in range(100)}
            self.assertIsNone(alternatives.build_reference(prices, yields))

    def test_negative_savings_yield_has_a_real_drawdown(self):
        found = alternatives.compare(result(), reference(yield_pct=lambda i: -3.65))
        self.assertLess(found.savings[0].ending_value, dec("200"))
        self.assertGreater(found.savings[0].max_drawdown, 0)

    def test_saved_comparison_does_not_change_with_new_reference_prices(self):
        plan = result()
        plan = replace(plan, comparison_snapshot=alternatives.snapshot(plan, reference()))
        summary = sweep_summary.summarize(plan)
        first = SweepResultView._compare(plan, summary, reference())[0]
        second = SweepResultView._compare(plan, summary, reference(fund=lambda i: 100 + i))[0]
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plans.jsonl"
            plan_history.append(path, plan, created_at_iso="2026-10-06T00:00:00Z")
            self.assertEqual(plan_history.load_all(path)[0].result.comparison_snapshot, plan.comparison_snapshot)


class SecurityAuditTests(unittest.TestCase):
    def test_redirects_never_send_a_second_request(self):
        for destination in ("http://127.0.0.1/", "https://other.invalid/", "/another"):
            sent = []
            class FakeSender(urllib.request.BaseHandler):
                handler_order = 100
                def https_open(self, request):
                    sent.append(request.full_url)
                    headers = Message()
                    headers["Location"] = destination
                    response = urllib.response.addinfourl(io.BytesIO(b""), headers, request.full_url, 302)
                    response.msg = "Found"
                    return response
            opener = no_redirect_opener()
            opener.add_handler(FakeSender())
            with self.assertRaises(urllib.error.HTTPError):
                opener.open("https://allowed.invalid/start")
            self.assertEqual(sent, ["https://allowed.invalid/start"])

    def test_url_policy_rejects_aliases_and_encoded_credential_keys(self):
        fetcher = UrllibGetFetcher(allowed_hosts=["allowed.invalid"])
        for url in ("https://@allowed.invalid/", "https://allowed.invalid:444/",
                    "https://allowed.invalid/?api%255fkey=x", "https://allowed.invalid/\n"):
            with self.assertRaises(ProviderError):
                fetcher._checked_url(FetchRequest(url))
        self.assertEqual(fetcher._checked_url(FetchRequest("https://ALLOWED.invalid/path", {"symbol": "BTCUSD"})),
                         "https://ALLOWED.invalid/path?symbol=BTCUSD")

    def test_pagination_preserves_legitimate_links_and_rejects_foreign_targets(self):
        current = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?cursor=one"
        self.assertEqual(pagination_url("?cursor=two", current, host="community-api.coinmetrics.io"),
                         current.replace("one", "two"))
        for value in ("http://community-api.coinmetrics.io/v4/timeseries/asset-metrics", "https://elsewhere.invalid/", "file:///tmp/x", 123):
            with self.assertRaises(ValueError):
                pagination_url(value, current, host="community-api.coinmetrics.io")

    def test_oversized_numeric_encodings_rejected_before_expansion(self):
        for value in ("1e1000000", "1e-1000000", "0e1000000", "1" * 600, Decimal("1e10000"), True):
            with self.assertRaises(ValueError):
                dec(value)
        with self.assertRaises(ValueError):
            format_decimal(Decimal("1e1000000"))
        self.assertEqual(dec("1e-8"), Decimal("0.00000001"))
        self.assertEqual(dec("-0.0125"), Decimal("-0.0125"))
        precise = Decimal("1.1234567890123456789012345678901234")
        self.assertEqual(Decimal(format_decimal(precise)), precise)

    def test_deep_json_is_rejected_and_jsonl_continues_after_bad_line(self):
        nested = "[" * 3000 + "0" + "]" * 3000
        with self.assertRaises(ProviderSchemaError):
            parse_records(nested, suffix=".json")
        records = parse_records('{"x":1}\n' + nested + '\n{"x":2}', suffix=".ndjson")
        self.assertEqual(records[0][1], {"x": 1})
        self.assertIsInstance(records[1][1], UnparseableRecord)
        self.assertEqual(records[2][1], {"x": 2})
        class Fetcher:
            def fetch(self, request):
                return FetchResponse(200, nested.encode(), {}, 0, 0)
        with self.assertRaises(ProviderSchemaError):
            fetch_json(Fetcher(), FetchRequest("https://allowed.invalid/"))

    def test_surplus_csv_is_quarantined_without_aborting_following_rows(self):
        provider = CoinLobsterAdapter()
        text = 'kind,time_ms,symbol,notional_usd,side\nlarge_trade,1000,BTC,60000,buy\nbogus,1000,BTC,1,buy,extra\nlarge_trade,1001,BTC,70000,sell\n'
        with IntelligenceStore.open(":memory:") as store:
            report = ingest_text(provider, text, suffix=".csv", source_ref="test", source_kind="file",
                                 store=store, policy=ReceiptPolicy.observed_now(),
                                 health=ProviderHealth(provider.name), clock=FixedClock(2000))
            self.assertEqual(report.events_inserted, 2)
            self.assertEqual(report.quarantined, 1)
            store.quarantine.add(provider=provider.name, reason="test", detail=None,
                                 raw_payload_reference=None, recorded_at=2000, record={"x": 1, None: [2]})
            self.assertEqual(store.quarantine.count(), 2)

    def test_csv_formulas_are_literal_and_negative_money_stays_numeric(self):
        labels = ["=1+1", "+1+1", "-1+1", "@SUM(1)", " \t=1+1", "\tplain", "普通, quoted"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.csv"
            rows = [{"reason": label, "amount": "-0.01"} for label in labels]
            _write_csv(path, rows)
            with path.open(newline="", encoding="utf-8") as handle:
                exported = list(csv.DictReader(handle))
            self.assertTrue(all(r["reason"].startswith("'") for r in exported[:-1]))
            self.assertEqual(exported[-1]["reason"], labels[-1])
            self.assertTrue(all(r["amount"] == "-0.01" for r in exported))
            self.assertEqual(rows[0]["reason"], "=1+1")


class FeedAuditTests(unittest.TestCase):
    def test_targets_must_match_base_asset_and_usd_quote(self):
        for market, asset in (("BTCUSDT", "BTC"), ("BTCUSD", "ETH"), ("BTCUSD", "btc")):
            with self.assertRaises(ValueError):
                PollTarget(market, asset)

    def test_same_trade_id_in_different_markets_is_not_a_duplicate(self):
        h = Harness(self)
        for market, asset in (("BTCUSD", "BTC"), ("ETHUSD", "ETH")):
            target = PollTarget(market, asset)
            for page, now in (([raw(a=100)], T0), ([raw(a=101, q="3", t=T0 + 1000)], T0 + 2000)):
                poll(h.adapter, FakeFetcher([page]), h.store, target, policy=h.policy,
                     health=h.health, clock=FixedClock(now))
        self.assertEqual(h.store.events.count(), 2)

    def test_gap_and_bad_row_leave_cursor_unchanged_and_keep_raw_evidence(self):
        h = Harness(self)
        h.poll([[raw(a=100)]], now=T0)
        for page in ([raw(a=102)], [raw(a=101), {"a": 102, "p": "bad"}]):
            with self.assertRaises(ProviderSchemaError):
                h.poll([page], now=T0 + 5000)
            self.assertEqual(h.cursor(), "100")
        self.assertEqual(h.store.quarantine.count(), 2)
        self.assertEqual(h.store.events.count(), 0)
        self.assertGreaterEqual(h.store.raw.count(), 3)

    def test_adjacent_batches_cover_the_time_between_their_trades(self):
        h = Harness(self)
        h.poll([[raw(a=100)]], now=T0)
        h.poll([[raw(a=101, t=T0 + 1000)]], now=T0 + 5000)
        h.poll([[raw(a=102, t=T0 + 2000)]], now=T0 + 6000)
        self.assertTrue(h.store.coverage.index().covers(h.adapter.name, "executed_trades",
                                                       T0 + 1000, T0 + 2000, asset="BTC"))

    def test_receipts_are_after_fetch_not_at_poll_start(self):
        h = Harness(self)
        h.poll([[raw(a=100)]], now=T0)
        class Clock:
            value = T0 + 1000
            def now_ms(self): return self.value
        clock = Clock()
        class SlowFetcher(FakeFetcher):
            def fetch(self, request):
                response = super().fetch(request)
                clock.value += 2000
                return response
        poll(h.adapter, SlowFetcher([[raw(a=101, q="3", t=T0 + 1000)]]), h.store, h.target,
             policy=h.policy, health=h.health, clock=clock)
        event = next(h.store.events.iter_all())
        self.assertEqual(event.system_received_time, T0 + 3000)

    def test_health_survives_process_restart(self):
        health = ProviderHealth("provider")
        health.record_failure(1000, "schema")
        health.record_failure(2000, "provider_error")
        restored = ProviderHealth.from_snapshot("provider", health.snapshot(2000))
        self.assertEqual(restored.consecutive_failures, 2)
        self.assertEqual(restored.schema_failures, 1)
        self.assertEqual(restored.last_attempt_at, 2000)


class LogAuditTests(unittest.TestCase):
    def test_torn_paper_log_preserves_next_append_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ticks.jsonl"
            ticklog.append(path, ticklog.TickLogEntry(1, "OK"))
            with path.open("a") as handle:
                handle.write('{"at":')
            ticklog.append(path, ticklog.TickLogEntry(2, "OK"))
            with self.assertLogs("crypto_research.jsonl", level="WARNING"):
                self.assertEqual([r.at for r in ticklog.read_all(path)], [1, 2])


class ReviewBoundaryTests(unittest.TestCase):
    def test_oversized_csv_header_is_schema_error(self):
        with self.assertRaises(ProviderSchemaError):
            parse_records("x" * (csv.field_size_limit() + 1) + "\n1\n", suffix=".csv")

    def test_nonfinite_retry_delay_is_ignored(self):
        from external_intelligence.base.transport import _retry_after_ms
        for value in ("inf", "1e309", "nan"):
            self.assertIsNone(_retry_after_ms({"retry-after": value}))
        self.assertEqual(_retry_after_ms({"retry-after": "2"}), 2000)

    def test_nested_json_public_success_and_error_are_domain_errors(self):
        from crypto_research.binanceus.transport import PublicTransport
        from crypto_research.errors import TransportError
        from .test_public_client import FakeResponse, FakeOpener
        body = ("[" * 3000 + "0" + "]" * 3000).encode()
        response = FakeResponse(None)
        response._body = body
        with self.assertRaises(TransportError):
            PublicTransport(opener=FakeOpener([response]), max_retries=1, sleep=lambda _: None).get("/api/v3/time")
        error = urllib.error.HTTPError("https://api.binance.us/api/v3/time", 400, "bad", {}, io.BytesIO(body))
        with self.assertRaises(TransportError):
            PublicTransport(opener=FakeOpener([error]), max_retries=1, sleep=lambda _: None).get("/api/v3/time")


    def test_nested_json_signed_success_and_error_are_domain_errors(self):
        from account_tools.signing import AccountTransportError
        from .test_account_tools import FakeResponse, transport_with
        body = ("[" * 3000 + "0" + "]" * 3000).encode()
        response = FakeResponse(None)
        response._body = body
        transport, _ = transport_with([response])
        with self.assertRaises(AccountTransportError):
            transport.get("/api/v3/account")
        error = urllib.error.HTTPError("https://api.binance.us/api/v3/account", 400, "bad", {}, io.BytesIO(body))
        transport, _ = transport_with([error])
        with self.assertRaises(AccountTransportError):
            transport.get("/api/v3/account")


class PaperIdentityAuditTests(unittest.TestCase):
    def test_changed_source_identity_forces_fresh_pair(self):
        from unittest.mock import patch
        from crypto_research.experiments.runner import input_identity
        from .test_paper_trading_runner import RehearsalHarness
        from .paper_trading_helpers import START as PAPER_START
        h = RehearsalHarness(self)
        h.client.add_day(["60000", "3000", "150"])
        first = h.tick(PAPER_START + DAY_MS + 100)
        def changed(config):
            identity = input_identity(config)
            identity["code_sha256"] = "different-source"
            return identity
        with patch("paper_trading.runner.input_identity", side_effect=changed):
            second = h.tick(PAPER_START + DAY_MS + 5000)
        self.assertEqual(second.status, "OK")
        self.assertFalse(second.rehearsal.reused)
        self.assertFalse(second.benchmark.reused)
        self.assertNotEqual(first.rehearsal.run_id, second.rehearsal.run_id)
        self.assertEqual(len(h.registry.list_runs()), 4)

    def test_failed_digest_is_not_reused(self):
        from unittest.mock import Mock
        from paper_trading.runner import _find_by_digest
        registry = Mock()
        registry.list_runs.return_value = [{"config_sha256": "a" * 16, "status": "FAILED", "run_id": "failed"}]
        self.assertIsNone(_find_by_digest(registry, "a" * 64))
        registry.load.assert_not_called()

    def test_event_horizon_requires_exact_complete_endpoint(self):
        from scripts.event_study import forward_change
        dates = [0, DAY_MS, 3 * DAY_MS]
        levels = [dec("100"), dec("110"), dec("120")]
        self.assertIsNone(forward_change(dates, levels, 0, 2))
        self.assertIsNone(forward_change(dates, levels, 2, 7))
        self.assertAlmostEqual(forward_change(dates, levels, 0, 3), 20.0)


if __name__ == "__main__":
    unittest.main()
