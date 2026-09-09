"""Regression tests for the Russell 2000 point-in-time universe (issue #2).

The universe came from the latest IWM holdings file, so regenerating an older
report silently applied today's membership. These tests pin snapshot reuse and
the explicit survivorship-bias warning. Fully offline: the "fetch" callable is
a stub and is asserted to stay unused when a snapshot exists.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from russell2000_market_report import universe_snapshot

REPORT_DATE = date(2026, 6, 30)
TODAY = date(2026, 9, 9)


def _fetch(symbols, *, calls=None, holdings_as_of="Sep 08 2026"):
    def fetch():
        if calls is not None:
            calls.append(1)
        return {
            "symbols": list(symbols),
            "source": "ishares-latest-holdings",
            "holdings_as_of": holdings_as_of,
        }

    return fetch


class UniverseSnapshotTests(unittest.TestCase):
    def test_snapshot_is_written_on_first_run(self) -> None:
        with TemporaryDirectory() as tmp:
            result = universe_snapshot.resolve_universe(
                tmp, REPORT_DATE, _fetch(["abc", "def"]), today=REPORT_DATE
            )
            path = universe_snapshot.snapshot_path(tmp, REPORT_DATE)
            self.assertTrue(path.exists())
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(payload["symbols"], ["ABC", "DEF"])
            self.assertEqual(payload["report_date"], REPORT_DATE.isoformat())
            self.assertEqual(result["symbols"], frozenset({"ABC", "DEF"}))

    def test_rerun_reuses_the_snapshot_and_does_not_refetch(self) -> None:
        with TemporaryDirectory() as tmp:
            calls: list[int] = []
            universe_snapshot.resolve_universe(
                tmp, REPORT_DATE, _fetch(["ABC"], calls=calls), today=REPORT_DATE
            )
            self.assertEqual(len(calls), 1)
            # Membership changed since the report date; the rerun must not use it.
            result = universe_snapshot.resolve_universe(
                tmp, REPORT_DATE, _fetch(["XYZ"], calls=calls), today=TODAY
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(result["symbols"], frozenset({"ABC"}))
            self.assertTrue(result["from_snapshot"])
            self.assertTrue(result["point_in_time"])

    def test_historical_date_without_snapshot_is_flagged(self) -> None:
        with TemporaryDirectory() as tmp:
            result = universe_snapshot.resolve_universe(
                tmp, REPORT_DATE, _fetch(["ABC"]), today=TODAY
            )
            self.assertFalse(result["point_in_time"])
            self.assertTrue(result["warnings"])
            self.assertIn("survivorship", result["warnings"][0].lower())

    def test_same_day_run_is_point_in_time_without_warnings(self) -> None:
        with TemporaryDirectory() as tmp:
            result = universe_snapshot.resolve_universe(
                tmp, TODAY, _fetch(["ABC"]), today=TODAY
            )
            self.assertTrue(result["point_in_time"])
            self.assertEqual(result["warnings"], [])

    def test_snapshots_are_isolated_per_report_date(self) -> None:
        with TemporaryDirectory() as tmp:
            universe_snapshot.resolve_universe(
                tmp, date(2026, 6, 30), _fetch(["ABC"]), today=date(2026, 6, 30)
            )
            universe_snapshot.resolve_universe(
                tmp, date(2026, 7, 31), _fetch(["XYZ"]), today=date(2026, 7, 31)
            )
            first = universe_snapshot.load_snapshot(tmp, date(2026, 6, 30))
            second = universe_snapshot.load_snapshot(tmp, date(2026, 7, 31))
            self.assertEqual(first["symbols"], ["ABC"])
            self.assertEqual(second["symbols"], ["XYZ"])

    def test_missing_snapshot_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsNone(universe_snapshot.load_snapshot(tmp, REPORT_DATE))

    def test_corrupt_or_empty_snapshot_is_ignored(self) -> None:
        with TemporaryDirectory() as tmp:
            path = universe_snapshot.snapshot_path(tmp, REPORT_DATE)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{oops", encoding="utf-8")
            self.assertIsNone(universe_snapshot.load_snapshot(tmp, REPORT_DATE))
            path.write_text(json.dumps({"symbols": []}), encoding="utf-8")
            self.assertIsNone(universe_snapshot.load_snapshot(tmp, REPORT_DATE))
            # A broken snapshot must not break the run; it refetches instead.
            result = universe_snapshot.resolve_universe(
                tmp, REPORT_DATE, _fetch(["ABC"]), today=REPORT_DATE
            )
            self.assertEqual(result["symbols"], frozenset({"ABC"}))

    def test_empty_holdings_fail_loudly(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                universe_snapshot.resolve_universe(
                    tmp, REPORT_DATE, _fetch([]), today=REPORT_DATE
                )

    def test_holdings_as_of_is_preserved_in_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            universe_snapshot.resolve_universe(
                tmp, REPORT_DATE, _fetch(["ABC"], holdings_as_of="Jun 30 2026"), today=REPORT_DATE
            )
            result = universe_snapshot.resolve_universe(
                tmp, REPORT_DATE, _fetch(["ABC"]), today=TODAY
            )
            self.assertEqual(result["holdings_as_of"], "Jun 30 2026")


if __name__ == "__main__":
    unittest.main()
