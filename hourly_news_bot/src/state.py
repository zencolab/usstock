"""Crawler dedupe state with explicit delivery stages.

Previously ``main.py`` wrote every new item into ``seen`` and saved the state
*before* uploading to the Drive gateway. If the upload failed, the next run
still treated those items as processed and skipped them, so the batch was lost
for good.

The state now distinguishes two stages:

``pending``    fetched and rendered, not confirmed delivered yet.
``delivered``  confirmed delivered; safe to skip forever (until pruned).

Items are only skipped when they are ``delivered``, so a failed upload is
retried on the next run. ``pending`` entries are kept (not lost) so a partially
successful run can be reconciled.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

STATE_VERSION = 2
PENDING = "pending"
DELIVERED = "delivered"
DEFAULT_RETENTION_DAYS = 30
# A pending item that was never confirmed is retried, but not forever.
DEFAULT_PENDING_RETENTION_DAYS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def empty_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "items": {}}


def migrate_state(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise any historical state layout into the staged layout.

    Version 1 stored ``{"seen": {key: iso_timestamp}}`` with no delivery
    information. Those keys had already been suppressed by the old code, so
    they are migrated as ``delivered`` to avoid re-sending old news.
    """

    if not isinstance(raw, Mapping):
        return empty_state()
    items = raw.get("items")
    if isinstance(items, Mapping):
        normalised: dict[str, Any] = {}
        for key, entry in items.items():
            if isinstance(entry, Mapping):
                status = str(entry.get("status") or PENDING)
                normalised[str(key)] = {
                    "status": status if status in {PENDING, DELIVERED} else PENDING,
                    "first_seen_at": entry.get("first_seen_at") or entry.get("updated_at"),
                    "updated_at": entry.get("updated_at") or entry.get("first_seen_at"),
                    "attempts": int(entry.get("attempts") or 0),
                }
            else:
                normalised[str(key)] = {
                    "status": DELIVERED,
                    "first_seen_at": str(entry),
                    "updated_at": str(entry),
                    "attempts": 0,
                }
        return {"version": STATE_VERSION, "items": normalised}

    legacy_seen = raw.get("seen")
    if isinstance(legacy_seen, Mapping):
        return {
            "version": STATE_VERSION,
            "items": {
                str(key): {
                    "status": DELIVERED,
                    "first_seen_at": str(value),
                    "updated_at": str(value),
                    "attempts": 0,
                }
                for key, value in legacy_seen.items()
            },
        }
    return empty_state()


def load_state(path: Path | str) -> dict[str, Any]:
    file_path = Path(path)
    if not file_path.exists():
        return empty_state()
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_state()
    return migrate_state(raw)


def save_state(path: Path | str, state: Mapping[str, Any]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(dict(state), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    # Atomic replace so an interrupted run cannot leave a truncated state file.
    tmp.replace(file_path)


def is_delivered(state: Mapping[str, Any], key: str) -> bool:
    entry = (state.get("items") or {}).get(key)
    return bool(entry) and entry.get("status") == DELIVERED


def delivered_keys(state: Mapping[str, Any]) -> set[str]:
    return {
        key
        for key, entry in (state.get("items") or {}).items()
        if isinstance(entry, Mapping) and entry.get("status") == DELIVERED
    }


def mark_pending(
    state: dict[str, Any], keys: Iterable[str], *, now: datetime | None = None
) -> dict[str, Any]:
    """Record that items were fetched but not yet confirmed delivered."""

    stamp = (now or _now()).isoformat()
    items = state.setdefault("items", {})
    for key in keys:
        entry = items.get(key)
        if isinstance(entry, Mapping) and entry.get("status") == DELIVERED:
            continue
        previous = entry if isinstance(entry, Mapping) else {}
        items[key] = {
            "status": PENDING,
            "first_seen_at": previous.get("first_seen_at") or stamp,
            "updated_at": stamp,
            "attempts": int(previous.get("attempts") or 0) + 1,
        }
    return state


def mark_delivered(
    state: dict[str, Any], keys: Iterable[str], *, now: datetime | None = None
) -> dict[str, Any]:
    """Record confirmed delivery. Only these items are skipped later."""

    stamp = (now or _now()).isoformat()
    items = state.setdefault("items", {})
    for key in keys:
        previous = items.get(key) if isinstance(items.get(key), Mapping) else {}
        items[key] = {
            "status": DELIVERED,
            "first_seen_at": previous.get("first_seen_at") or stamp,
            "updated_at": stamp,
            "attempts": int(previous.get("attempts") or 0),
        }
    return state


def prune(
    state: dict[str, Any],
    *,
    delivered_days: int = DEFAULT_RETENTION_DAYS,
    pending_days: int = DEFAULT_PENDING_RETENTION_DAYS,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference = now or _now()
    delivered_cutoff = reference - timedelta(days=delivered_days)
    pending_cutoff = reference - timedelta(days=pending_days)
    items = state.get("items") or {}
    kept: dict[str, Any] = {}
    for key, entry in items.items():
        if not isinstance(entry, Mapping):
            continue
        stamp = _parse(entry.get("updated_at")) or _parse(entry.get("first_seen_at"))
        if stamp is None:
            kept[key] = dict(entry)
            continue
        cutoff = delivered_cutoff if entry.get("status") == DELIVERED else pending_cutoff
        if stamp >= cutoff:
            kept[key] = dict(entry)
    state["items"] = kept
    return state
