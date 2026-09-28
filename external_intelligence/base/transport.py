"""A GET-only fetcher that cannot carry credentials.

This is generic infrastructure. It contains no provider URL and no default
endpoint: a provider is only ever fetched from a host that a human has listed in
``allowed_hosts`` after checking that provider's terms.

It is deliberately unable to authenticate, which is what keeps this zone's claim
("cannot authenticate") true rather than merely unexercised:

* HTTPS only, GET only, no request body, no cookies;
* a URL carrying a user name, a password or a credential-looking query key is
  refused before anything is sent;
* no header other than a fixed User-Agent can be set;
* the response size is capped and the wait is bounded.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol

from ..errors import ProviderError, ProviderRateLimited, ProviderSchemaError, ProviderTimeout
from .clock import Clock, SystemClock
from .ratelimit import RateLimiter

USER_AGENT = "crypto-research-external-intelligence/1 (read-only research)"
DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 5_000_000

# Query keys that would mean someone is trying to authenticate. Refused, not stripped.
_CREDENTIAL_KEYS = frozenset(
    {
        "api_key", "apikey", "api-key", "key", "token", "access_token", "auth",
        "authorization", "secret", "signature", "sig", "password", "passwd",
    }
)


@dataclass(frozen=True, slots=True)
class FetchRequest:
    url: str
    params: Mapping[str, str] = field(default_factory=dict)
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass(frozen=True, slots=True)
class FetchResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    received_at_ms: int
    latency_ms: int


class Fetcher(Protocol):
    def fetch(self, request: FetchRequest) -> FetchResponse: ...


class UrllibGetFetcher:
    """Standard-library GET with a host allow-list, a timeout and a size cap."""

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        clock: Clock | None = None,
        opener: Callable[..., Any] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self.allowed_hosts = frozenset(host.lower() for host in allowed_hosts)
        self._clock = clock or SystemClock()
        self._opener = opener or urllib.request.urlopen
        self._max_bytes = max_bytes
        self._limiter = rate_limiter

    def fetch(self, request: FetchRequest) -> FetchResponse:
        url = self._checked_url(request)
        if self._limiter is not None:
            self._limiter.acquire_or_raise()
        started = self._clock.now_ms()
        began = time.monotonic()
        http_request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            response = self._opener(http_request, timeout=request.timeout_s)
            try:
                body = self._read_limited(response)
                status = int(getattr(response, "status", None) or response.getcode())
                headers = {k.lower(): v for k, v in dict(response.headers).items()}
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except urllib.error.HTTPError as exc:
            self._raise_for_http_error(exc)
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeout(f"timed out after {request.timeout_s:g}s") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeout(f"timed out after {request.timeout_s:g}s") from exc
            raise ProviderError(f"connection failed: {exc.reason}") from exc
        except OSError as exc:
            raise ProviderError(f"network error: {exc}") from exc

        if status == 429:
            raise ProviderRateLimited("provider answered 429", _retry_after_ms(headers))
        if status >= 400:
            raise ProviderError(f"provider answered HTTP {status}")
        latency_ms = int((time.monotonic() - began) * 1000)
        return FetchResponse(status, body, headers, started + latency_ms, latency_ms)

    # -- internals -----------------------------------------------------------

    def _checked_url(self, request: FetchRequest) -> str:
        parts = urllib.parse.urlsplit(request.url)
        if parts.scheme != "https":
            raise ProviderError("only https URLs are fetched")
        if parts.username or parts.password:
            raise ProviderError("credentials in a URL are not supported in this zone")
        host = (parts.hostname or "").lower()
        if host not in self.allowed_hosts:
            raise ProviderError(f"host {host!r} is not on the allow-list")
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        pairs += list(request.params.items())
        for key, _ in pairs:
            if key.lower() in _CREDENTIAL_KEYS:
                raise ProviderError(
                    f"query key {key!r} looks like a credential; authenticated "
                    "fetching is not supported in this zone"
                )
        query = urllib.parse.urlencode(pairs)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))

    def _read_limited(self, response: Any) -> bytes:
        data = response.read(self._max_bytes + 1)
        if len(data) > self._max_bytes:
            raise ProviderError(f"response larger than {self._max_bytes} bytes")
        return data

    @staticmethod
    def _raise_for_http_error(exc: urllib.error.HTTPError) -> None:
        if exc.code == 429:
            headers = {k.lower(): v for k, v in dict(exc.headers or {}).items()}
            raise ProviderRateLimited("provider answered 429", _retry_after_ms(headers)) from exc
        raise ProviderError(f"provider answered HTTP {exc.code}") from exc


def _retry_after_ms(headers: Mapping[str, str]) -> int | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return int(float(value) * 1000)
    except ValueError:
        return None


def fetch_json(fetcher: Fetcher, request: FetchRequest) -> Any:
    """Fetch and decode a JSON body, or raise a provider error."""
    response = fetcher.fetch(request)
    try:
        return json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderSchemaError("reply was not valid UTF-8 JSON") from exc
