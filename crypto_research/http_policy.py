"""Restrict outbound requests before sending them; never follow redirects."""

from __future__ import annotations

import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def no_redirect_opener():
    return urllib.request.build_opener(NoRedirect())


def pagination_url(value, current: str, *, host: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("invalid pagination URL")
    url = urllib.parse.urljoin(current, value)
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != host or "@" in parts.netloc
            or parts.port not in (None, 443)
            or parts.path != "/v4/timeseries/asset-metrics" or parts.fragment):
        raise ValueError("pagination must remain on the expected HTTPS origin and endpoint")
    return url
