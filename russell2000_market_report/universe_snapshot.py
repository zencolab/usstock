"""Point-in-time snapshots of the Russell 2000 tracking universe.

The universe is derived from the *latest* published IWM holdings file. Using it
to regenerate an older report introduces survivorship bias: today's holdings
exclude companies that were delisted or removed after the report date, and
include names that were not yet members.

This module stores one snapshot per report date and prefers a stored snapshot
whenever a historical report is requested. When no snapshot exists for a past
date the caller still gets a usable universe, but the metadata says so
explicitly (``point_in_time = False``) so the report can carry the caveat
instead of implying a clean backtest.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

SNAPSHOT_DIR_NAME = "universe-snapshots"
SNAPSHOT_VERSION = 1


def snapshot_dir(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / SNAPSHOT_DIR_NAME


def snapshot_path(cache_dir: Path | str, report_date: date) -> Path:
    return snapshot_dir(cache_dir) / f"russell2000-{report_date.isoformat()}.json"


def save_snapshot(
    cache_dir: Path | str,
    report_date: date,
    symbols: Iterable[str],
    *,
    source: str,
    holdings_as_of: str | None = None,
) -> Path:
    """Persist the universe used for ``report_date`` so it can be replayed."""

    path = snapshot_path(cache_dir, report_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SNAPSHOT_VERSION,
        "report_date": report_date.isoformat(),
        "source": source,
        "holdings_as_of": holdings_as_of,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "symbols": sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(cache_dir: Path | str, report_date: date) -> dict[str, Any] | None:
    path = snapshot_path(cache_dir, report_date)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    symbols = payload.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        return None
    payload["symbols"] = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    return payload


def resolve_universe(
    cache_dir: Path | str,
    report_date: date,
    fetch_latest: Callable[[], Mapping[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Return the universe for ``report_date`` plus provenance metadata.

    ``fetch_latest`` returns a mapping with at least ``symbols`` and may carry
    ``source`` / ``holdings_as_of``. It is only called when no snapshot can be
    reused, which keeps historical reruns fully offline and reproducible.
    """

    reference_day = today or datetime.now(timezone.utc).date()
    stored = load_snapshot(cache_dir, report_date)
    if stored:
        return {
            "symbols": frozenset(stored["symbols"]),
            "source": stored.get("source") or "snapshot",
            "holdings_as_of": stored.get("holdings_as_of"),
            "report_date": report_date.isoformat(),
            "point_in_time": True,
            "from_snapshot": True,
            "warnings": [],
        }

    latest = dict(fetch_latest() or {})
    symbols = frozenset(
        str(symbol).strip().upper()
        for symbol in (latest.get("symbols") or [])
        if str(symbol).strip()
    )
    if not symbols:
        raise ValueError("Latest Russell 2000 holdings returned no symbols")

    is_historical = report_date < reference_day
    warnings: list[str] = []
    if is_historical:
        warnings.append(
            f"No stored universe snapshot for {report_date.isoformat()}; using the latest "
            "IWM holdings, so the ranking may contain survivorship bias."
        )

    # Always record the snapshot, so the *next* rerun of this date is exact.
    save_snapshot(
        cache_dir,
        report_date,
        symbols,
        source=str(latest.get("source") or "ishares-latest-holdings"),
        holdings_as_of=latest.get("holdings_as_of"),
    )

    return {
        "symbols": symbols,
        "source": str(latest.get("source") or "ishares-latest-holdings"),
        "holdings_as_of": latest.get("holdings_as_of"),
        "report_date": report_date.isoformat(),
        "point_in_time": not is_historical,
        "from_snapshot": False,
        "warnings": warnings,
    }
