"""Exception hierarchy."""


class CryptoResearchError(Exception):
    """Base for every error this package raises."""


class ConfigError(CryptoResearchError):
    """Configuration was missing, malformed, or internally inconsistent."""


class TransportError(CryptoResearchError):
    """Network/HTTP level failure talking to Binance.US."""


class BinanceUSAPIError(TransportError):
    """Binance.US returned a structured error payload."""

    def __init__(self, status: int, code: int | None, message: str, url: str) -> None:
        super().__init__(f"Binance.US HTTP {status} code={code}: {message} ({url})")
        self.status = status
        self.code = code
        self.api_message = message
        self.url = url


class RateLimitError(BinanceUSAPIError):
    """HTTP 429 or 418. Back off; 418 means the IP is temporarily banned."""


class FilterParseError(CryptoResearchError):
    """exchangeInfo could not be interpreted into usable trading rules."""


class ValuationError(CryptoResearchError):
    """A portfolio could not be valued because a required rate was absent.

    Raised rather than silently assuming a rate. In particular, USDT is never
    assumed to be worth 1 USD.
    """


class LookaheadError(CryptoResearchError):
    """A strategy tried to read data that did not exist at decision time."""


class DeterminismError(CryptoResearchError):
    """Something non-deterministic leaked into a simulated run."""


class LiveTradingAttemptError(CryptoResearchError):
    """Tripwire: something tried to reach a live-execution path. There is none."""
