"""Runtime defaults shared by the report entrypoints.

The scheduled workflows always export ``ALPACA_FEED`` with a default of
``iex``, but the hybrid pipeline defaulted to ``sip`` when the variable was
absent. A local run therefore silently requested a different feed than CI, and
the two feeds do not cover the same trades. Both callers now resolve the feed
through one function with one default.
"""

from __future__ import annotations

import os
from typing import Mapping

# Matches `ALPACA_FEED: ${{ vars.ALPACA_FEED || 'iex' }}` in the workflows.
DEFAULT_ALPACA_FEED = "iex"

SUPPORTED_ALPACA_FEEDS = frozenset({"iex", "sip", "otc", "delayed_sip", "boats", "overnight"})


def resolve_alpaca_feed(env: Mapping[str, str] | None = None) -> str:
    """Return the Alpaca market-data feed to use.

    ``env`` defaults to ``os.environ`` and is injectable so the behaviour can be
    asserted without mutating the process environment.
    """

    source = os.environ if env is None else env
    feed = str(source.get("ALPACA_FEED", "") or "").strip().lower()
    if not feed:
        return DEFAULT_ALPACA_FEED
    if feed not in SUPPORTED_ALPACA_FEEDS:
        raise ValueError(
            f"ALPACA_FEED={feed!r} is not a supported Alpaca feed; "
            f"expected one of {', '.join(sorted(SUPPORTED_ALPACA_FEEDS))}"
        )
    return feed
