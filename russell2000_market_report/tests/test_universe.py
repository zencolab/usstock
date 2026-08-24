from __future__ import annotations

import unittest

import pandas as pd

from russell2000_market_report.universe import (
    filter_grouped_frame,
    normalize_symbol,
    parse_ishares_holdings_csv,
    validate_universe,
)


class Russell2000UniverseTests(unittest.TestCase):
    def test_parse_ishares_csv_finds_header_and_equities(self) -> None:
        text = """iShares Russell 2000 ETF\nFund Holdings as of,Aug 21 2026\n\nTicker,Name,Sector,Asset Class,Weight (%)\nABC,Alpha,Industrials,Equity,0.2\nCWEN/A,Clearway,Utilities,Equity,0.1\nUSD,US Dollar,Cash and/or Derivatives,Cash,1.0\n-,Future,Other,Futures,0.1\n"""
        symbols, as_of = parse_ishares_holdings_csv(text)
        self.assertEqual(symbols, frozenset({"ABC", "CWEN.A"}))
        self.assertEqual(as_of, "Aug 21 2026")

    def test_normalize_symbol_handles_share_classes(self) -> None:
        self.assertEqual(normalize_symbol("brk/b"), "BRK.B")
        self.assertEqual(normalize_symbol("bf-b"), "BF.B")
        self.assertIsNone(normalize_symbol("--"))

    def test_filter_grouped_frame_preserves_massive_symbols(self) -> None:
        frame = pd.DataFrame(
            {
                "symbol": ["ABC", "CWEN.A", "SPY"],
                "close": [10.0, 20.0, 30.0],
            }
        )
        filtered = filter_grouped_frame(frame, {"ABC", "CWEN.A"})
        self.assertEqual(filtered["symbol"].tolist(), ["ABC", "CWEN.A"])

    def test_validate_universe_rejects_bad_count(self) -> None:
        with self.assertRaises(ValueError):
            validate_universe(["ABC"], minimum=2, maximum=10)


if __name__ == "__main__":
    unittest.main()
