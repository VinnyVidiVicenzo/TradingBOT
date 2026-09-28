"""The read-only account reader, and the boundary around it.

No test here touches the network or needs a credential.
"""

from __future__ import annotations

import ast
import json
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

from account_tools.client import BinanceUSAccountClient, fees_for_symbols
from account_tools.credentials import (
    KEY_ENV_VAR,
    SECRET_ENV_VAR,
    Credentials,
    MissingCredentials,
    load_credentials,
    redact,
)
from account_tools.reporting import (
    render_fee_block,
    render_holdings_block,
    render_key_check,
)
from account_tools.signing import (
    ACCOUNT_READ_ENDPOINTS,
    BASE_URL,
    AccountTransportError,
    SignedReadOnlyTransport,
    build_query,
    sign_query,
)
from crypto_research.decimals import dec

REPO_ROOT = Path(__file__).parent.parent

# The official Binance SIGNED-endpoint documentation example. Pinning it proves
# this implementation matches the documented scheme, rather than merely being
# self-consistent.
DOC_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
DOC_QUERY = (
    "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
    "&recvWindow=5000&timestamp=1499827319559"
)
DOC_SIGNATURE = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"

CREDS = Credentials(key="testkey1234", secret="topsecret")

ACCOUNT_PAYLOAD: dict[str, Any] = {
    "makerCommission": 15,
    "takerCommission": 15,
    "canTrade": True,
    "canWithdraw": False,
    "canDeposit": True,
    "accountType": "SPOT",
    "permissions": ["SPOT"],
    "updateTime": 1_700_000_000_000,
    "commissionRates": {
        "maker": "0.00150000",
        "taker": "0.00250000",
        "buyer": "0.00000000",
        "seller": "0.00000000",
    },
    "balances": [
        {"asset": "BTC", "free": "0.00210000", "locked": "0.00000000"},
        {"asset": "BNB", "free": "0.31000000", "locked": "0.00000000"},
        {"asset": "USD", "free": "42.17000000", "locked": "0.00000000"},
        {"asset": "USDT", "free": "10.00000000", "locked": "0.00000000"},
        {"asset": "ETH", "free": "0.00000000", "locked": "0.00000000"},
    ],
}

TRADE_FEE_PAYLOAD = [
    {"symbol": "BTCUSD", "makerCommission": "0.00150000", "takerCommission": "0.00250000"},
    {"symbol": "ETHUSD", "makerCommission": "0.00150000", "takerCommission": "0.00250000"},
    {"symbol": "SOLUSD", "makerCommission": "0.00000000", "takerCommission": "0.00000000"},
]


class FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = Message()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FakeOpener:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.urls: list[str] = []
        self.methods: list[str] = []
        self.headers: list[dict[str, str]] = []

    def open(self, request, timeout=None):  # noqa: ANN001 - urllib signature
        self.urls.append(request.full_url)
        self.methods.append(request.get_method())
        self.headers.append(dict(request.headers))
        if not self._responses:
            raise AssertionError(f"no queued response for {request.full_url}")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def transport_with(responses: list[Any]) -> tuple[SignedReadOnlyTransport, FakeOpener]:
    opener = FakeOpener(responses)
    transport = SignedReadOnlyTransport(
        CREDS, opener=opener, time_source=lambda: 1_700_000_000_000
    )
    return transport, opener


# --------------------------------------------------------------------------
# signing
# --------------------------------------------------------------------------


class SigningTests(unittest.TestCase):
    def test_matches_the_official_documented_test_vector(self) -> None:
        self.assertEqual(sign_query(DOC_QUERY, DOC_SECRET), DOC_SIGNATURE)

    def test_signature_is_deterministic(self) -> None:
        self.assertEqual(
            sign_query("symbol=BTCUSD", "topsecret"),
            "8586c352fc578d1d304cae2f561e656da0540d689c8d64028183802edb9fd25b",
        )

    def test_query_is_sorted_and_drops_nones(self) -> None:
        self.assertEqual(
            build_query({"symbol": "BTCUSD", "a": 1, "skip": None}), "a=1&symbol=BTCUSD"
        )

    def test_signature_covers_exactly_the_query_that_is_sent(self) -> None:
        transport, opener = transport_with([FakeResponse(ACCOUNT_PAYLOAD)])
        transport.get("/api/v3/account")
        url = opener.urls[0]
        query, _, signature = url.partition("?")[2].rpartition("&signature=")
        self.assertEqual(signature, sign_query(query, CREDS.secret))

    def test_timestamp_and_recv_window_are_injected(self) -> None:
        transport, opener = transport_with([FakeResponse(ACCOUNT_PAYLOAD)])
        transport.get("/api/v3/account")
        self.assertIn("timestamp=1700000000000", opener.urls[0])
        self.assertIn("recvWindow=5000", opener.urls[0])

    def test_an_out_of_range_recv_window_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            SignedReadOnlyTransport(CREDS, recv_window_ms=90_000)


class TransportSafetyTests(unittest.TestCase):
    def test_only_get_is_ever_issued(self) -> None:
        transport, opener = transport_with([FakeResponse(ACCOUNT_PAYLOAD)])
        transport.get("/api/v3/account")
        self.assertEqual(opener.methods, ["GET"])

    def test_a_path_outside_the_whitelist_is_refused_before_any_request(self) -> None:
        transport, opener = transport_with([])
        with self.assertRaises(AccountTransportError) as ctx:
            transport.get("/api/v3/" + "order")
        self.assertIn("not a permitted account-read endpoint", str(ctx.exception))
        self.assertEqual(opener.urls, [], "nothing should have been sent")

    def test_the_whitelist_contains_no_trading_or_withdrawal_path(self) -> None:
        for endpoint in ACCOUNT_READ_ENDPOINTS:
            lowered = endpoint.lower()
            for forbidden in ("order", "withdraw", "transfer", "convert", "oco", "otc"):
                self.assertNotIn(
                    forbidden, lowered, f"{endpoint} looks like more than a read"
                )

    def test_the_whitelist_is_small_on_purpose(self) -> None:
        # Every entry widens what a leaked key could reach through this tool.
        self.assertLessEqual(len(ACCOUNT_READ_ENDPOINTS), 6)

    def test_the_base_url_is_binance_us(self) -> None:
        self.assertEqual(BASE_URL, "https://api.binance.us")

    def test_the_api_key_header_is_sent_but_the_secret_is_not(self) -> None:
        transport, opener = transport_with([FakeResponse(ACCOUNT_PAYLOAD)])
        transport.get("/api/v3/account")
        headers = {k.lower(): v for k, v in opener.headers[0].items()}
        self.assertEqual(headers.get("x-mbx-apikey"), CREDS.key)
        self.assertNotIn(CREDS.secret, opener.urls[0])
        self.assertNotIn(CREDS.secret, json.dumps(headers))

    def test_api_errors_are_redacted(self) -> None:
        body = json.dumps(
            {"code": -1022, "msg": f"bad signature for key {CREDS.key}"}
        ).encode()
        error = urllib.error.HTTPError(
            url="https://api.binance.us/api/v3/account", code=401,
            msg="Unauthorized", hdrs=Message(), fp=_BytesLike(body),
        )
        transport, _ = transport_with([error])
        with self.assertRaises(AccountTransportError) as ctx:
            transport.get("/api/v3/account")
        self.assertNotIn(CREDS.key, str(ctx.exception))
        self.assertIn("redacted-key", str(ctx.exception))


class _BytesLike:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        return None


# --------------------------------------------------------------------------
# credentials
# --------------------------------------------------------------------------


class CredentialHandlingTests(unittest.TestCase):
    def test_repr_never_contains_the_secret(self) -> None:
        self.assertNotIn("topsecret", repr(CREDS))
        self.assertNotIn("topsecret", str(CREDS))
        self.assertIn("redacted", repr(CREDS))

    def test_fingerprint_shows_only_the_last_four_characters(self) -> None:
        self.assertEqual(CREDS.fingerprint, "...1234")
        self.assertNotIn("testkey", CREDS.fingerprint)

    def test_redact_strips_both_values(self) -> None:
        text = f"key={CREDS.key} secret={CREDS.secret}"
        cleaned = redact(text, CREDS)
        self.assertNotIn(CREDS.key, cleaned)
        self.assertNotIn(CREDS.secret, cleaned)

    def test_empty_credentials_are_refused(self) -> None:
        with self.assertRaises(MissingCredentials):
            Credentials(key="", secret="x")

    def test_missing_env_vars_produce_setup_instructions(self) -> None:
        import os

        saved = {k: os.environ.pop(k, None) for k in (KEY_ENV_VAR, SECRET_ENV_VAR)}
        try:
            # Must raise immediately, never wait for input. An earlier version
            # prompted here and hung the whole suite.
            with self.assertRaises(MissingCredentials) as ctx:
                load_credentials()
            message = str(ctx.exception)
            self.assertIn(KEY_ENV_VAR, message)
            self.assertIn("reading permission only", message)
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_the_loader_never_prompts(self) -> None:
        # Guard against reintroducing a hang: the loader must not touch
        # getpass or the terminal, whatever the environment looks like. An
        # earlier version prompted from here and blocked the whole suite.
        import inspect

        from account_tools import credentials as module

        source = inspect.getsource(module.load_credentials)
        self.assertNotIn("getpass", source)
        self.assertNotIn("prompt_for_credentials", source)
        self.assertNotIn("isatty", source)

    def test_credentials_are_read_from_the_environment_only(self) -> None:
        import os

        saved = {k: os.environ.get(k) for k in (KEY_ENV_VAR, SECRET_ENV_VAR)}
        os.environ[KEY_ENV_VAR] = "abc1234"
        os.environ[SECRET_ENV_VAR] = "shhh"
        try:
            creds = load_credentials()
            self.assertEqual(creds.key, "abc1234")
            self.assertEqual(creds.fingerprint, "...1234")
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------


class AccountReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport, self.opener = transport_with([FakeResponse(ACCOUNT_PAYLOAD)])
        self.client = BinanceUSAccountClient(self.transport)

    def test_commission_rates_are_decimals(self) -> None:
        snapshot = self.client.account()
        self.assertEqual(snapshot.commission.maker, dec("0.0015"))
        self.assertEqual(snapshot.commission.taker, dec("0.0025"))
        self.assertFalse(snapshot.commission.is_zero)

    def test_zero_balances_are_filtered_from_the_snapshot_view(self) -> None:
        snapshot = self.client.account()
        assets = [b.asset for b in snapshot.non_zero_balances()]
        self.assertEqual(assets, ["BNB", "BTC", "USD", "USDT"])
        self.assertNotIn("ETH", assets)

    def test_locked_balance_counts_toward_the_total(self) -> None:
        payload = json.loads(json.dumps(ACCOUNT_PAYLOAD))
        payload["balances"][0]["locked"] = "0.00090000"
        transport, _ = transport_with([FakeResponse(payload)])
        snapshot = BinanceUSAccountClient(transport).account()
        self.assertEqual(snapshot.balance_of("BTC"), dec("0.003"))

    def test_account_flags_are_surfaced(self) -> None:
        snapshot = self.client.account()
        self.assertTrue(snapshot.can_trade)
        self.assertFalse(snapshot.can_withdraw)
        self.assertEqual(snapshot.permissions, ("SPOT",))

    def test_trade_fees_are_parsed_and_sorted(self) -> None:
        transport, _ = transport_with([FakeResponse(TRADE_FEE_PAYLOAD)])
        fees = BinanceUSAccountClient(transport).trade_fees()
        self.assertEqual([f.symbol for f in fees], ["BTCUSD", "ETHUSD", "SOLUSD"])
        self.assertEqual(fees[2].taker, dec("0"))

    def test_fees_can_be_narrowed_to_the_symbols_in_use(self) -> None:
        transport, _ = transport_with([FakeResponse(TRADE_FEE_PAYLOAD)])
        fees = fees_for_symbols(BinanceUSAccountClient(transport), ["BTCUSD", "SOLUSD"])
        self.assertEqual(sorted(fees), ["BTCUSD", "SOLUSD"])


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


class ReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        transport, _ = transport_with([FakeResponse(ACCOUNT_PAYLOAD)])
        self.snapshot = BinanceUSAccountClient(transport).account()

    def test_fee_block_is_parseable_toml_with_real_rates(self) -> None:
        import tomllib

        block = render_fee_block(self.snapshot, {}, observed_iso="2026-09-27T00:00:00Z")
        parsed = tomllib.loads(block)
        self.assertEqual(parsed["costs"]["fees"]["maker_rate"], "0.0015")
        self.assertEqual(parsed["costs"]["fees"]["taker_rate"], "0.0025")
        self.assertIn("MEASURED", parsed["costs"]["fees"]["source"])

    def test_fee_block_pins_symbols_whose_rates_differ(self) -> None:
        import tomllib

        from account_tools.client import SymbolTradeFee

        per_symbol = {
            "SOLUSD": SymbolTradeFee("SOLUSD", maker=dec("0"), taker=dec("0")),
            "BTCUSD": SymbolTradeFee("BTCUSD", maker=dec("0.0015"), taker=dec("0.0025")),
        }
        block = render_fee_block(
            self.snapshot, per_symbol, observed_iso="2026-09-27T00:00:00Z"
        )
        parsed = tomllib.loads(block)
        pinned = parsed["costs"]["fees"]["per_symbol"]
        self.assertIn("SOLUSD", pinned, "a differing rate must be pinned")
        self.assertNotIn("BTCUSD", pinned, "a matching rate needs no override")

    def test_fee_note_warns_that_the_rate_is_a_point_in_time_read(self) -> None:
        block = render_fee_block(
            self.snapshot, {}, observed_iso="2026-09-27T00:00:00Z", window="2025-2026"
        )
        self.assertIn("NOT necessarily the rate that applied", block)
        self.assertIn("2025-2026", block)

    def test_holdings_block_separates_cash_from_positions(self) -> None:
        import tomllib

        block = render_holdings_block(
            self.snapshot, observed_iso="2026-09-27T00:00:00Z"
        )
        parsed = tomllib.loads(block)
        portfolio = parsed["portfolio"]
        # USD and USDT are cash, and stay distinct.
        self.assertEqual(portfolio["initial_cash"]["USD"], "42.17")
        self.assertEqual(portfolio["initial_cash"]["USDT"], "10")
        # BTC and BNB are positions, never cash.
        held = {h["asset"] for h in portfolio["starting_holdings"]}
        self.assertEqual(held, {"BTC", "BNB"})

    def test_holdings_block_emits_no_cost_basis(self) -> None:
        block = render_holdings_block(self.snapshot, observed_iso="2026-09-27T00:00:00Z")
        import tomllib

        parsed = tomllib.loads(block)
        for holding in parsed["portfolio"]["starting_holdings"]:
            self.assertNotIn(
                "cost_basis_per_unit", holding,
                "the venue reports no basis, so none may be invented",
            )

    def test_holdings_block_warns_that_a_stablecoin_is_not_a_dollar(self) -> None:
        block = render_holdings_block(self.snapshot, observed_iso="2026-09-27T00:00:00Z")
        self.assertIn("stablecoin balance is NOT dollars", block)

    def test_key_check_states_that_key_permissions_cannot_be_verified(self) -> None:
        text = render_key_check(self.snapshot, {})
        self.assertIn("describe the ACCOUNT, not this API key", text)
        self.assertIn("cannot verify your key is read-only", text)


# --------------------------------------------------------------------------
# the boundary
# --------------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


# The research *engine*: everything a backtest run touches. The two modules
# left out are the user-facing entry point and the auditor, which exist
# precisely to compose the two zones and are not part of a run.
ENGINE_EXCLUDED = frozenset({"cli.py", "safety.py", "__main__.py"})


def _engine_modules() -> list[Path]:
    return [
        path
        for path in sorted((REPO_ROOT / "crypto_research").rglob("*.py"))
        if path.name not in ENGINE_EXCLUDED
    ]


class ZoneBoundaryTests(unittest.TestCase):
    """The research engine must not be able to reach live account state."""

    def test_the_engine_never_imports_account_tools(self) -> None:
        offenders = []
        for path in _engine_modules():
            for module in _imported_modules(path):
                if module.split(".")[0] == "account_tools":
                    offenders.append(f"{path.name} imports {module}")
        self.assertEqual(
            offenders, [],
            "the backtester must not be able to read live account state: a run "
            "has to be reproducible from stored data alone",
        )

    def test_the_engine_does_not_import_the_cli_or_the_auditor(self) -> None:
        # This closes the transitive path. safety.py and cli.py may reference
        # account_tools; the engine may not reference them, so no chain of
        # imports reaches live account state from inside a run.
        offenders = []
        for path in _engine_modules():
            for module in _imported_modules(path):
                tail = module.rsplit(".", 1)[-1]
                if tail in {"cli", "safety"} and module.startswith("crypto_research"):
                    offenders.append(f"{path.name} imports {module}")
        self.assertEqual(offenders, [])

    def test_the_engine_reaches_account_tools_through_no_chain_at_all(self) -> None:
        # Belt and braces: import every engine module in a fresh interpreter
        # with account_tools made un-importable. If any chain existed, this
        # fails.
        import subprocess
        import sys

        script = (
            "import sys\n"
            "class Blocker:\n"
            "    def find_module(self, name, path=None):\n"
            "        if name.split('.')[0] == 'account_tools':\n"
            "            raise AssertionError('engine reached account_tools: ' + name)\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocker())\n"
            "import crypto_research.backtest, crypto_research.strategies\n"
            "import crypto_research.experiments, crypto_research.datafeed\n"
            "import crypto_research.storage, crypto_research.config\n"
            "print('clean')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean", result.stdout)

    def test_the_safety_module_imports_account_tools_only_lazily(self) -> None:
        # safety.py reports the account-tools whitelist, so it needs the name --
        # but only inside a function, so importing crypto_research never pulls
        # the signing code into the process.
        source = (REPO_ROOT / "crypto_research" / "safety.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                name = getattr(node, "module", "") or ""
                self.assertNotEqual(
                    name.split(".")[0], "account_tools",
                    "account_tools must not be imported at module level",
                )

    def test_account_tools_does_not_import_the_backtester(self) -> None:
        # One-way dependency: the reader may reuse pure helpers, but must not
        # reach into the simulation, the strategies or the registry.
        forbidden = {"crypto_research.backtest", "crypto_research.strategies",
                     "crypto_research.experiments", "crypto_research.datafeed",
                     "crypto_research.storage"}
        offenders = []
        for path in sorted((REPO_ROOT / "account_tools").rglob("*.py")):
            for module in _imported_modules(path):
                if module in forbidden or any(
                    module.startswith(f + ".") for f in forbidden
                ):
                    offenders.append(f"{path.name} imports {module}")
        self.assertEqual(offenders, [])

    def test_account_tools_reuses_the_research_decimal_helpers(self) -> None:
        # A rate read here and a rate used in a backtest must be the same kind
        # of number, so the reader deliberately shares the Decimal helpers.
        modules = _imported_modules(REPO_ROOT / "account_tools" / "client.py")
        self.assertIn("crypto_research.decimals", modules)


class AuditZoneTests(unittest.TestCase):
    def test_the_whole_repository_passes_the_zone_audit(self) -> None:
        from crypto_research.safety import audit_repository

        report = audit_repository(REPO_ROOT)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(
            sorted(report.zones),
            ["account-tools", "desktop-app", "external-intel", "paper-trading", "research", "support"],
        )

    def test_the_research_zone_still_forbids_signing(self) -> None:
        import tempfile

        from crypto_research.safety import RESEARCH_RULES, audit

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "leak.py"
            bad.write_text("import hmac\n", encoding="utf-8")
            report = audit(Path(tmp), rules=RESEARCH_RULES)
            self.assertFalse(report.ok)

    def test_the_account_tools_zone_permits_signing(self) -> None:
        import tempfile

        from crypto_research.safety import ACCOUNT_TOOLS_RULES, audit

        with tempfile.TemporaryDirectory() as tmp:
            ok_file = Path(tmp) / "signer.py"
            ok_file.write_text("import hmac\n", encoding="utf-8")
            report = audit(Path(tmp), rules=ACCOUNT_TOOLS_RULES)
            self.assertTrue(report.ok, report.summary())

    def test_the_account_tools_zone_still_forbids_order_placement(self) -> None:
        import tempfile

        from crypto_research.safety import ACCOUNT_TOOLS_RULES, audit

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "trader.py"
            bad.write_text(
                f'PATH = "/api/v3/{"order"}"\n', encoding="utf-8"
            )
            report = audit(Path(tmp), rules=ACCOUNT_TOOLS_RULES)
            self.assertFalse(report.ok, "order placement must be forbidden everywhere")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
