"""Regression tests for the news-bot delivery state (review issue #3).

Before the fix, main.py marked every fetched item as "seen" and saved the
state file *before* the Drive upload, so a failed upload silently dropped the
batch. These tests pin the staged behaviour: items are only skipped after a
confirmed delivery.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from src import state as crawler_state


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)


class DeliveryStateTests(unittest.TestCase):
    def test_pending_items_are_not_treated_as_delivered(self) -> None:
        state = crawler_state.mark_pending(crawler_state.empty_state(), ["a", "b"], now=NOW)
        self.assertFalse(crawler_state.is_delivered(state, "a"))
        self.assertFalse(crawler_state.is_delivered(state, "b"))
        self.assertEqual(crawler_state.delivered_keys(state), set())

    def test_failed_upload_leaves_items_retryable(self) -> None:
        # Run 1: fetched and rendered, upload raised, so nothing was confirmed.
        state = crawler_state.mark_pending(crawler_state.empty_state(), ["a"], now=NOW)
        # Run 2 must not skip the item.
        self.assertNotIn("a", crawler_state.delivered_keys(state))

    def test_successful_upload_marks_delivered(self) -> None:
        state = crawler_state.mark_pending(crawler_state.empty_state(), ["a"], now=NOW)
        state = crawler_state.mark_delivered(state, ["a"], now=NOW)
        self.assertTrue(crawler_state.is_delivered(state, "a"))
        self.assertEqual(crawler_state.delivered_keys(state), {"a"})

    def test_pending_retry_increments_attempts_and_keeps_first_seen(self) -> None:
        state = crawler_state.mark_pending(crawler_state.empty_state(), ["a"], now=NOW)
        later = NOW + timedelta(hours=2)
        state = crawler_state.mark_pending(state, ["a"], now=later)
        entry = state["items"]["a"]
        self.assertEqual(entry["attempts"], 2)
        self.assertEqual(entry["first_seen_at"], NOW.isoformat())
        self.assertEqual(entry["updated_at"], later.isoformat())

    def test_delivered_items_are_never_downgraded_to_pending(self) -> None:
        state = crawler_state.mark_delivered(crawler_state.empty_state(), ["a"], now=NOW)
        state = crawler_state.mark_pending(state, ["a"], now=NOW + timedelta(hours=2))
        self.assertTrue(crawler_state.is_delivered(state, "a"))

    def test_legacy_seen_state_migrates_to_delivered(self) -> None:
        legacy = {"seen": {"old-key": NOW.isoformat()}}
        migrated = crawler_state.migrate_state(legacy)
        self.assertEqual(migrated["version"], crawler_state.STATE_VERSION)
        self.assertTrue(crawler_state.is_delivered(migrated, "old-key"))

    def test_migrate_handles_missing_and_broken_input(self) -> None:
        self.assertEqual(crawler_state.migrate_state(None), crawler_state.empty_state())
        self.assertEqual(crawler_state.migrate_state({}), crawler_state.empty_state())

    def test_prune_keeps_recent_and_drops_expired(self) -> None:
        state = crawler_state.empty_state()
        state = crawler_state.mark_delivered(state, ["fresh"], now=NOW - timedelta(days=1))
        state = crawler_state.mark_delivered(state, ["stale"], now=NOW - timedelta(days=45))
        state = crawler_state.mark_pending(state, ["pending-old"], now=NOW - timedelta(days=10))
        pruned = crawler_state.prune(state, now=NOW)
        self.assertIn("fresh", pruned["items"])
        self.assertNotIn("stale", pruned["items"])
        self.assertNotIn("pending-old", pruned["items"])

    def test_state_round_trips_through_disk_atomically(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "crawler-state.json"
            state = crawler_state.mark_delivered(
                crawler_state.empty_state(), ["a"], now=NOW
            )
            crawler_state.save_state(path, state)
            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            reloaded = crawler_state.load_state(path)
            self.assertTrue(crawler_state.is_delivered(reloaded, "a"))

    def test_corrupt_state_file_does_not_crash_the_run(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "crawler-state.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(crawler_state.load_state(path), crawler_state.empty_state())

    def test_saved_state_is_readable_json_with_stages(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "crawler-state.json"
            state = crawler_state.mark_pending(crawler_state.empty_state(), ["a"], now=NOW)
            crawler_state.save_state(path, state)
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["items"]["a"]["status"], crawler_state.PENDING)


if __name__ == "__main__":
    unittest.main()
