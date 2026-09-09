from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from russell2000_market_report import universe, runtime

ROOT = Path(__file__).resolve().parents[2]


class RussellReviewRegressionTests(unittest.TestCase):
    def parse_in_fresh_process(self, arguments):
        code = "import json,sys; import russell2000_market_report.main as app; sys.argv=['test']+json.loads(sys.argv[1]); a=app.engine.parse_args(); print(json.dumps([str(a.output),str(a.data_output)]))"
        result = subprocess.run([sys.executable, "-c", code, json.dumps(arguments)], cwd=ROOT, check=True, capture_output=True, text=True, timeout=60)
        return json.loads(result.stdout)

    def test_default_outputs_are_isolated(self):
        self.assertEqual(self.parse_in_fresh_process([]), [str(ROOT / "russell2000_market_report/site"), str(ROOT / "russell2000_market_report/output")])

    def test_explicit_output_paths_are_preserved(self):
        self.assertEqual(self.parse_in_fresh_process(["--output", "custom-site", "--data-output", "custom-data"]), ["custom-site", "custom-data"])

    def snapshot(self, as_of):
        return universe.UniverseSnapshot(frozenset({"AAA"}), "test", as_of, "2026-08-10T00:00:00+00:00", "test")

    def test_future_snapshot_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "newer"):
            universe.validate_snapshot_as_of(self.snapshot("Aug 11 2026"), date(2026, 8, 10))

    def test_undated_historical_snapshot_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "as-of"):
            universe.validate_snapshot_as_of(self.snapshot(""), date(2026, 8, 10), historical=True)

    def test_dated_historical_snapshot_is_accepted(self):
        universe.validate_snapshot_as_of(self.snapshot("Aug 7 2026"), date(2026, 8, 10), historical=True)

    def test_historical_run_requires_explicit_constituents_before_download(self):
        class Massive:
            def grouped_daily(self, day): raise AssertionError("must not fetch")
        ns = {"live_payload": Mock(), "write_outputs": Mock(), "MassiveClient": Massive, "resolve_trade_dates": lambda _: (date(2026, 8, 20), date(2026, 8, 19))}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), patch.object(runtime, "load_russell2000_universe") as download:
            runtime.install(ns, Path(folder))
            with self.assertRaisesRegex(RuntimeError, "Historical reports require"):
                ns["live_payload"](SimpleNamespace(report_date=date(2026, 8, 10), top_n=1))
            download.assert_not_called()

    def test_patch_is_restored_when_inner_pipeline_fails(self):
        class Massive:
            def grouped_daily(self, day): return None
        original = Massive.grouped_daily
        ns = {"live_payload": Mock(side_effect=ValueError("inner failure")), "write_outputs": Mock(), "MassiveClient": Massive, "resolve_trade_dates": lambda _: (date(2026, 8, 10), date(2026, 8, 7))}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True), patch.object(runtime, "load_russell2000_universe", return_value=self.snapshot("Aug 10 2026")):
            runtime.install(ns, Path(folder))
            with self.assertRaisesRegex(ValueError, "inner failure"):
                ns["live_payload"](SimpleNamespace(report_date=date(2026, 8, 10), top_n=1))
            self.assertIs(Massive.grouped_daily, original)


if __name__ == "__main__": unittest.main()
