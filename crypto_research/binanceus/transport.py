"""HTTP transport for Binance.US public endpoints.

Standard library only (urllib). The package has no third-party dependencies,
which keeps the supply-chain surface of a financial research tool as small as
it can reasonably be.

Safety properties enforced here, not merely documented:

* GET is the only method this transport can issue.
* Only paths present in PUBLIC_ENDPOINTS may be requested.
* No Authorization header, no API-key header, no request signing, and no
  reading of any credential environment variable.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping

from ..errors import BinanceUSAPIError, RateLimitError, TransportError
from ..timeutil import now_ms
from .constants import BASE_URL, PUBLIC_ENDPOINTS, USER_AGENT

_FORBIDDEN_HEADER_PREFIXES = ("authorization", "x-mbx-api" + "key")  # safety-audit: allow


@dataclass(slots=True)
class Response:
    """A decoded public response plus the metadata we care about."""

    payload: Any
    status: int
    url: str
    received_at: int
    used_weight_1m: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RateLimitState:
    """Best-effort local view of consumed IP weight.

    Binance.US reports usage on every response; we mirror it so a long
    backfill can pause before it trips a 429 rather than after.
    """

    used_weight_1m: int = 0
    last_seen_at: int = 0


class PublicTransport:
    """Read-only GET transport with retry and rate-limit awareness."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        *,
        timeout: float = 20.0,
        max_retries: int = 4,
        weight_ceiling_1m: int = 4800,
        opener: Any | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        # Stay below the documented 6000/min so a shared IP has headroom.
        self.weight_ceiling_1m = weight_ceiling_1m
        self.rate_limit = RateLimitState()
        self._opener = opener or urllib.request.build_opener()
        self._sleep = sleep
        self._random = random.Random(0)  # jitter only; never touches simulation

    # -- public API --------------------------------------------------------

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> Response:
        """Issue a GET against a whitelisted public path."""
        if path not in PUBLIC_ENDPOINTS:
            raise TransportError(
                f"{path!r} is not a permitted public endpoint. V1 may only read "
                f"public market data; one of {sorted(PUBLIC_ENDPOINTS)} was expected."
            )
        query = _encode_params(params or {})
        url = f"{self.base_url}{path}" + (f"?{query}" if query else "")
        self._respect_weight_ceiling()
        return self._get_with_retries(url)

    # -- internals ---------------------------------------------------------

    def _respect_weight_ceiling(self) -> None:
        if self.rate_limit.used_weight_1m < self.weight_ceiling_1m:
            return
        elapsed = now_ms() - self.rate_limit.last_seen_at
        remaining_ms = max(0, 60_000 - elapsed)
        if remaining_ms:
            self._sleep(remaining_ms / 1000)
        self.rate_limit.used_weight_1m = 0

    def _get_with_retries(self, url: str) -> Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._get_once(url)
            except RateLimitError as exc:
                last_error = exc
                # 418 means the IP is banned; backing off harder is the only
                # correct response, and hammering makes the ban longer.
                base = 60.0 if exc.status == 418 else 2.0
                self._sleep(base * (2**attempt) + self._random.random())
            except TransportError as exc:
                last_error = exc
                self._sleep(0.5 * (2**attempt) + self._random.random())
        raise TransportError(f"GET {url} failed after {self.max_retries} attempts: {last_error}")

    def _get_once(self, url: str) -> Response:
        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", USER_AGENT)
        request.add_header("Accept", "application/json")
        _assert_no_credentials(request)
        try:
            with self._opener.open(request, timeout=self.timeout) as raw:
                body = raw.read()
                status = raw.status
                headers = {k.lower(): v for k, v in raw.headers.items()}
        except urllib.error.HTTPError as exc:  # 4xx / 5xx
            body = exc.read()
            status = exc.code
            headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
            self._record_weight(headers)
            raise _api_error(status, body, url) from exc
        except urllib.error.URLError as exc:
            raise TransportError(f"GET {url} failed: {exc.reason}") from exc

        self._record_weight(headers)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError(f"GET {url} returned non-JSON body") from exc
        return Response(
            payload=payload,
            status=status,
            url=url,
            received_at=now_ms(),
            used_weight_1m=self.rate_limit.used_weight_1m or None,
            headers=headers,
        )

    def _record_weight(self, headers: Mapping[str, str]) -> None:
        raw = headers.get("x-mbx-used-weight-1m") or headers.get("x-mbx-used-weight")
        if raw is None:
            return
        try:
            self.rate_limit.used_weight_1m = int(raw)
            self.rate_limit.last_seen_at = now_ms()
        except ValueError:  # pragma: no cover - malformed header
            pass


def _assert_no_credentials(request: urllib.request.Request) -> None:
    for name in request.headers:
        if name.lower().startswith(_FORBIDDEN_HEADER_PREFIXES):
            raise TransportError(
                "credential header detected on a public request; V1 must never "
                "authenticate against Binance.US"
            )


def _encode_params(params: Mapping[str, Any]) -> str:
    """Encode query parameters deterministically, dropping Nones.

    Sorted so that two identical logical requests produce byte-identical URLs,
    which makes the recorded fixtures and the request cache stable.
    """
    cleaned = {k: v for k, v in params.items() if v is not None}
    return urllib.parse.urlencode(sorted(cleaned.items()), safe="[]")


def _api_error(status: int, body: bytes, url: str) -> BinanceUSAPIError:
    code: int | None = None
    message = body.decode("utf-8", errors="replace")[:500]
    try:
        parsed = json.loads(body.decode("utf-8"))
        if isinstance(parsed, dict):
            code = parsed.get("code")
            message = str(parsed.get("msg", message))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    if status in (418, 429):
        return RateLimitError(status, code, message, url)
    return BinanceUSAPIError(status, code, message, url)
