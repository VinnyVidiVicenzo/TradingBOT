"""CoinLobster: executed whale trades, liquidations, funding and open interest.

Status: an importer for user-provided data only. Nothing is fetched.

What was checked (2026-09-28, coinlobster.com and its public GitHub README)
---------------------------------------------------------------------------
* The keyless free tier is an MCP interface (``https://coinlobster.com/mcp``) built
  for AI agents. MCP over HTTP uses POST, which this repository's audit forbids in
  every zone.
* An optional free API key unlocks a one-off credit grant for ``/api/ai/v1/*``
  REST endpoints. A key means authentication, which this zone cannot do.
* Full depth needs a paid plan.
* No response schema and no rate limit is documented.
* The terms (section 8) allow quoting individual figures with attribution and a
  link, and exclude "bulk extraction or systematic copying of a dataset".

Given that, no automated fetching was built, and nothing was scraped. Importing an
export is only appropriate if it was obtained under terms that allow research use
and storage; confirm that before importing anything. Live ingestion needs a
human's decision (a written agreement or a licence) first.

The record format read is the project's own ``executed_activity_flat_v1`` (see
``base/executed_activity.py``), not CoinLobster's payload.
"""

from __future__ import annotations

from ..base.executed_activity import ExecutedActivityAdapter

LIVE_STATUS = (
    "off: not built. CoinLobster's terms exclude bulk extraction or systematic copying "
    "of a dataset; its keyless tier is an MCP interface that needs HTTP POST (forbidden "
    "by this repository's audit in every zone); its REST endpoints need an API key (this "
    "zone cannot authenticate); and it documents no response schema or rate limit. "
    "A licence or written agreement is needed before any automated access."
)
TERMS_NOTE = (
    "coinlobster.com/terms section 8: individual figures may be quoted with attribution "
    "and a link; bulk extraction or systematic copying of a dataset is excluded. Storing "
    "an export for research may fall under that exclusion. Confirm before importing."
)


class CoinLobsterAdapter(ExecutedActivityAdapter):
    name = "coinlobster"
    live_ingestion_status = LIVE_STATUS
    terms_note = TERMS_NOTE
