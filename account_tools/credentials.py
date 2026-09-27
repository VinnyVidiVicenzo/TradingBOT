"""Credential loading.

Credentials come from the environment and nowhere else. They are never read
from a config file, never written anywhere, and never logged. The only thing
this module will ever print about a key is the last four characters, so a
person can confirm *which* key is in use without the value being exposed.

Set them yourself, in your own shell, before running. Nothing in this
repository should ever contain the values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

KEY_ENV_VAR = "BINANCEUS_READ_API_KEY"
SECRET_ENV_VAR = "BINANCEUS_READ_API_SECRET"  # nosec - variable name, not a value


class MissingCredentials(RuntimeError):
    """Neither credential env var was set."""


@dataclass(frozen=True, slots=True)
class Credentials:
    """An API key and secret held in memory for the duration of one command.

    ``repr`` is overridden so that a stray print, log line, or traceback
    cannot leak the secret. Dataclass ``repr`` would have shown both fields.
    """

    key: str
    secret: str

    def __post_init__(self) -> None:
        if not self.key or not self.secret:
            raise MissingCredentials("both an API key and a secret are required")

    @property
    def fingerprint(self) -> str:
        """A safe identifier for the key: last four characters only."""
        return f"...{self.key[-4:]}" if len(self.key) >= 4 else "(too short)"

    def __repr__(self) -> str:
        return f"Credentials(key={self.fingerprint}, secret=<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


def load_credentials() -> Credentials:
    """Read credentials from the environment.

    Raises with setup instructions rather than prompting, because a prompt is
    a place a secret can end up in a shell history or a transcript.
    """
    key = os.environ.get(KEY_ENV_VAR, "").strip()
    secret = os.environ.get(SECRET_ENV_VAR, "").strip()
    if not key or not secret:
        missing = [
            name
            for name, value in ((KEY_ENV_VAR, key), (SECRET_ENV_VAR, secret))
            if not value
        ]
        raise MissingCredentials(
            "not set: "
            + ", ".join(missing)
            + ".\n\nSet them in your own shell before running. PowerShell:\n"
            f'  $env:{KEY_ENV_VAR} = "<paste key>"\n'
            f'  $env:{SECRET_ENV_VAR} = "<paste secret>"\n\n'
            "bash:\n"
            f'  export {KEY_ENV_VAR}="<paste key>"\n'
            f'  export {SECRET_ENV_VAR}="<paste secret>"\n\n'
            "Use a key created with reading permission only: no trading, no "
            "withdrawals, and IP-restricted if you can. Do not put the values "
            "in any file in this repository."
        )
    return Credentials(key=key, secret=secret)


def redact(text: str, credentials: Credentials) -> str:
    """Strip credential values out of text before it is shown or stored.

    A belt-and-braces pass for error messages: an API error echoing back a
    query string could otherwise carry the key, and a signature, into output.
    """
    cleaned = text.replace(credentials.secret, "<redacted-secret>")
    return cleaned.replace(credentials.key, "<redacted-key>")
