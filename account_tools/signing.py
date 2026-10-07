"""Signed GET transport for read-only Binance.US account endpoints.

Signing mechanics follow the official Binance.US documentation: HMAC-SHA256
over the percent-encoded query string, using the API secret as the key, with
the result appended as ``&signature=``. The API key travels in the
``X-MBX-APIKEY`` header. ``timestamp`` is required and ``recvWindow`` bounds
how long the request stays valid.

Three properties are enforced in code rather than trusted to the caller:

* **GET only.** There is no code path that issues any other method.
* **Whitelist only.** ``ACCOUNT_READ_ENDPOINTS`` holds five read-only account
  endpoints. A path outside it is refused before a request is built, and a
  test asserts the whitelist contains no order, cancel, withdraw, transfer or
  convert path.
* **Nothing leaks.** The secret is never logged; error text is redacted before
  it is raised.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from .credentials import Credentials, redact
from crypto_research.http_policy import no_redirect_opener

BASE_URL = "https://api.binance.us"

USER_AGENT = "crypto-research-account-tools/0.1 (read-only; no order placement)"

# Read-only account endpoints, with documented request weights. Deliberately
# minimal: only what is needed to read commission rates and balances, plus the
# two status endpoints used to sanity-check the key. Nothing is added here
# without a specific need, because every entry widens what a leaked key could
# reach through this tool.
ACCOUNT_READ_ENDPOINTS: Mapping[str, int] = MappingProxyType(
    {
        "/api/v3/account": 10,
        "/api/v3/account/commission": 20,
        "/sapi/v1/asset/tradeFee": 1,
        "/sapi/v1/account/status": 1,
        "/sapi/v1/account/apiTradingStatus": 1,
    }
)

DEFAULT_RECV_WINDOW_MS = 5_000
MAX_RECV_WINDOW_MS = 60_000


class AccountTransportError(RuntimeError):
    """Network or HTTP failure on a signed request."""


class AccountAPIError(AccountTransportError):
    """Binance.US returned a structured error on a signed request."""

    def __init__(self, status: int, code: int | None, message: str, path: str) -> None:
        super().__init__(f"Binance.US HTTP {status} code={code} on {path}: {message}")
        self.status = status
        self.code = code
        self.api_message = message
        self.path = path


@dataclass(frozen=True, slots=True)
class SignedResponse:
    payload: Any
    status: int
    path: str


def sign_query(query: str, secret: str) -> str:
    """HMAC-SHA256 of an already-encoded query string, as lowercase hex.

    The query string must be percent-encoded *before* it reaches this function:
    the documentation is explicit that the signature covers the encoded
    payload, and signing the unencoded form produces a valid-looking signature
    that the venue rejects.
    """
    return hmac.new(
        secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def build_query(params: Mapping[str, Any]) -> str:
    """Percent-encode parameters deterministically, dropping Nones.

    Sorted so the same logical request always produces the same string, which
    keeps signatures reproducible and makes a mismatch debuggable.
    """
    cleaned = {k: v for k, v in params.items() if v is not None}
    return urllib.parse.urlencode(sorted(cleaned.items()))


class SignedReadOnlyTransport:
    """Issues signed GETs against the account-read whitelist. Nothing else."""

    def __init__(
        self,
        credentials: Credentials,
        *,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        recv_window_ms: int = DEFAULT_RECV_WINDOW_MS,
        opener: Any | None = None,
        time_source: Any | None = None,
    ) -> None:
        if not 0 < recv_window_ms <= MAX_RECV_WINDOW_MS:
            raise ValueError(f"recv_window_ms must be 1..{MAX_RECV_WINDOW_MS}")
        self._credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.recv_window_ms = recv_window_ms
        self._opener = opener or no_redirect_opener()
        self._time_source = time_source or _local_time_ms

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> SignedResponse:
        """Issue one signed GET against a whitelisted read-only endpoint."""
        if path not in ACCOUNT_READ_ENDPOINTS:
            raise AccountTransportError(
                f"{path!r} is not a permitted account-read endpoint. This tool "
                f"reads account state only; permitted paths are "
                f"{sorted(ACCOUNT_READ_ENDPOINTS)}."
            )

        payload = dict(params or {})
        payload["timestamp"] = self._time_source()
        payload["recvWindow"] = self.recv_window_ms

        query = build_query(payload)
        signature = sign_query(query, self._credentials.secret)
        url = f"{self.base_url}{path}?{query}&signature={signature}"

        request = urllib.request.Request(url, method="GET")
        request.add_header("X-MBX-API" + "KEY", self._credentials.key)
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", "application/json")

        try:
            with self._opener.open(request, timeout=self.timeout) as raw:
                body = raw.read()
                status = raw.status
        except urllib.error.HTTPError as exc:
            body = exc.read()
            raise self._api_error(exc.code, body, path) from None
        except urllib.error.URLError as exc:
            raise AccountTransportError(
                f"GET {path} failed: {self._redact(str(exc.reason))}"
            ) from None

        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            raise AccountTransportError(f"GET {path} returned a non-JSON body") from None
        return SignedResponse(payload=decoded, status=status, path=path)

    # -- internals ---------------------------------------------------------

    def _api_error(self, status: int, body: bytes, path: str) -> AccountAPIError:
        code: int | None = None
        message = self._redact(body.decode("utf-8", errors="replace")[:500])
        try:
            parsed = json.loads(body.decode("utf-8"))
            if isinstance(parsed, dict):
                code = parsed.get("code")
                message = self._redact(str(parsed.get("msg", message)))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            pass
        return AccountAPIError(status, code, message, path)

    def _redact(self, text: str) -> str:
        return redact(text, self._credentials)


def _local_time_ms() -> int:
    from datetime import datetime, timezone

    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
