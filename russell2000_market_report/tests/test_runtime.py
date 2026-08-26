from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import requests

from russell2000_market_report.runtime import (
    _is_massive_entitlement_error,
    alpaca_grouped_daily,
    brand_output,
)


class FakeAlpacaClient:
    feed = "iex"

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], date, date, int]] = []

    def histories(
        self,
        symbols,
        start: date,
        end: date,
        *,
        chunk_size: int,
    ):
        self.calls.append((list(symbols), start, end, chunk_size))
        return {
            "ABC": pd.DataFrame(
                [{"date": start, "close": 12.5, "volume": 1_000}]
            ),
            "XYZ": pd.DataFrame(columns=["date", "close", "volume"]),
        }


class Russell2000RuntimeTests(unittest.TestCase):
    def test_brand_output_keeps_project_visibly_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            site = Path(temporary_directory)
            (site / "index.html").write_text(
                '<html><body><h1>美股收盘日报</h1><div class="meta-row"></div>'
                '<footer>来源：Massive</footer></body></html>',
                encoding="utf-8",
            )
            brand_output(
                site,
                ["Alpaca IEX daily bars (Massive HTTP 403 fallback)"],
            )
            content = (site / "index.html").read_text(encoding="utf-8")
            self.assertIn("罗素 2000 收盘日报", content)
            self.assertIn("股票池：罗素 2000", content)
            self.assertIn("iShares IWM", content)
            self.assertIn("Alpaca IEX", content)

    def test_massive_403_activates_fallback(self) -> None:
        forbidden = requests.HTTPError(
            response=SimpleNamespace(status_code=403)
        )
        throttled = requests.HTTPError(
            response=SimpleNamespace(status_code=429)
        )
        self.assertTrue(_is_massive_entitlement_error(forbidden))
        self.assertFalse(_is_massive_entitlement_error(throttled))
        self.assertFalse(_is_massive_entitlement_error(RuntimeError("other")))

    def test_alpaca_grouped_daily_builds_ranking_frame(self) -> None:
        day = date(2026, 8, 25)
        client = FakeAlpacaClient()
        frame = alpaca_grouped_daily(client, ["abc", "xyz"], day)
        self.assertEqual(
            frame.to_dict("records"),
            [{"symbol": "ABC", "close": 12.5, "volume": 1_000.0}],
        )
        self.assertEqual(client.calls, [(["ABC", "XYZ"], day, day, 75)])


if __name__ == "__main__":
    unittest.main()
