"""Regression tests for the Russell 2000 project defects found while reviewing f7953bf.

They fail on the reviewed baseline and pass once the guarded fixes are applied:
1. The project reused the shared CLI defaults, so `site/` and `output/` were written
   to the repository root and collided with the whole-market report.
2. A historical trade date was ranked against the *latest* IWM holdings, and an
   undated or future constituent list was accepted without complaint.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import market_report as engine
from russell2000_market_report import main as app
from russell2000_market_report import runtime, universe


class RussellRegressionTests(unittest.TestCase):
    project = Path(app.__file__).resolve().parent

    def build_namespace(self) -> dict:
        namespace = dict(vars(engine))
        namespace["live_payload"] = MagicMock(name="engine_live_payload")
        namespace["write_outputs"] = MagicMock(name="engine_write_outputs")
        return namespace

    def build_config(self, report_date: date) -> SimpleNamespace:
        return SimpleNamespace(
            mode="live",
            report_date=report_date,
            previous_date=report_date - timedelta(days=1),
            top_n=1,
            months=6,
            min_price=1,
            min_dollar_volume=1,
            output=self.project / "site",
            data_output=self.project / "output",
        )

    def snapshot(self, as_of: str) -> SimpleNamespace:
        return SimpleNamespace(
            symbols=["AAA", "BBB"],
            source="test fixture",
            as_of=as_of,
            method="test fixture",
        )

    def test_cli_defaults_stay_inside_the_project(self):
        argv = ["russell2000_market_report.main", "--mode", "demo", "--date", "2026-08-10"]
        with patch.object(sys, "argv", argv):
            args = app.engine.parse_args()
        self.assertEqual(Path(args.output).resolve(), self.project / "site")
        self.assertEqual(Path(args.data_output).resolve(), self.project / "output")

    def test_historical_reports_require_authorized_constituents(self):
        namespace = self.build_namespace()
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(runtime, "load_russell2000_universe") as loader:
                runtime.install(namespace, self.project)
                with self.assertRaisesRegex(RuntimeError, "Historical reports require"):
                    namespace["live_payload"](self.build_config(date.today() - timedelta(days=60)))
                loader.assert_not_called()

    def test_future_constituents_are_rejected(self):
        namespace = self.build_namespace()
        future = self.snapshot((date.today() + timedelta(days=60)).isoformat())
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(runtime, "load_russell2000_universe", return_value=future):
                runtime.install(namespace, self.project)
                with self.assertRaisesRegex(RuntimeError, "newer than the report date"):
                    namespace["live_payload"](self.build_config(date.today()))

    def test_publishing_lag_stays_within_the_grace_window(self):
        universe.validate_snapshot_as_of(
            self.snapshot((date.today() + timedelta(days=2)).isoformat()),
            date.today(),
        )

    def test_dated_historical_constituents_are_accepted(self):
        universe.validate_snapshot_as_of(
            self.snapshot("2026-08-07"),
            date(2026, 8, 10),
            historical=True,
        )

    def test_undated_historical_constituents_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "as-of"):
            universe.validate_snapshot_as_of(
                self.snapshot(""),
                date(2026, 8, 10),
                historical=True,
            )

    def test_invalid_as_of_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Invalid constituent as-of date"):
            universe.validate_snapshot_as_of(self.snapshot("not-a-date"), date(2026, 8, 10))


if __name__ == "__main__":
    unittest.main()
