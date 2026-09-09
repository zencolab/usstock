from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import market_report as report
import hybrid_runtime
from hybrid_data import AlpacaClient
from premium_translation import CachedAiTranslator, bilingual_news


class ReviewRegressionTests(unittest.TestCase):
    day = date(2026, 8, 10)

    def config(self, root, mode="demo"):
        return report.RunConfig(mode, self.day, date(2026, 8, 7), 1, 6, 1, 1, root / "site", root / "output")

    def test_index_and_stock_escape_external_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = self.config(Path(folder))
            payload = report.demo_payload(cfg)
            probe = '<em data-probe="untrusted">company</em>'
            for stock in payload["gainers"] + payload["losers"]:
                stock["name"] = probe
                stock["description"] = probe
            report.add_charts(payload)
            report.write_outputs(cfg, payload)
            for path in [cfg.output / "index.html", *list((cfg.output / "stocks").glob("*.html"))]:
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(probe, text)
                self.assertIn("&lt;em", text)
                self.assertIn("<svg", text)

    def test_news_detail_escapes_model_output(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = self.config(Path(folder), "live")
            payload = report.demo_payload(cfg)
            probe = '<em data-probe="model">translated</em>'
            payload["gainers"][0]["news"] = [{"detail_file": "test.html", "detail_url": "../news/test.html", "headline_en": "English", "headline_zh": probe, "paragraphs": [{"en": "Summary", "zh": probe}], "url": "https://example.com/news"}]
            report.add_charts(payload)
            report.write_outputs(cfg, payload)
            text = (cfg.output / "news/test.html").read_text(encoding="utf-8")
            self.assertNotIn(probe, text)
            self.assertIn("&lt;em", text)

    def facts(self):
        past = {"form": "10-K", "start": "2025-01-01", "end": "2025-12-31", "filed": "2026-07-01", "val": 2}
        future = {**past, "filed": "2026-09-01", "val": 99}
        return {"facts": {"us-gaap": {"EarningsPerShareDiluted": {"units": {"USD/shares": [past, future]}}}, "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [{"end": "2026-06-30", "filed": "2026-07-01", "val": 100}, {"end": "2026-08-31", "filed": "2026-09-01", "val": 900}]}}}}}

    def test_fundamentals_exclude_future_filings(self):
        facts = report.SecClient.fundamentals(self.facts(), as_of=self.day)
        self.assertEqual(facts["eps"], 2)
        self.assertEqual(facts["shares_outstanding"], 100)

    def test_undated_facts_are_not_historical_evidence(self):
        data = self.facts()
        rows = data["facts"]["us-gaap"]["EarningsPerShareDiluted"]["units"]["USD/shares"]
        rows[0].pop("filed")
        self.assertIsNone(report.SecClient.fundamentals(data, as_of=self.day)["eps"])

    def test_no_cutoff_preserves_parser_compatibility(self):
        self.assertEqual(report.SecClient.fundamentals(self.facts())["eps"], 99)

    def test_ownership_window_uses_report_date(self):
        recent = {"form": ["SC 13G", "SC 13G", "4", "4", "4"], "filingDate": ["2026-07-01", "2026-09-01", "2026-07-01", "2026-05-01", "2026-08-11"], "accessionNumber": ["a", "b", "c", "d", "e"], "primaryDocument": ["a.htm"] * 5}
        result = report.SecClient.ownership_summary({"filings": {"recent": recent}}, "1", as_of=self.day)
        self.assertIn("含 1 份 13D/13G", result["summary"])
        self.assertIn("近 90 天含 1 份", result["summary"])
        self.assertTrue(all(row["filing_date"] <= self.day.isoformat() for row in result["filings"]))

    def test_alpaca_default_matches_workflow(self):
        self.assertEqual(AlpacaClient("test", "test").feed, "iex")

    def test_hybrid_propagates_as_of_and_default_feed(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = self.config(Path(folder), "live")
            class Massive:
                def __init__(self, *args): pass
                def grouped_daily(self, day):
                    return pd.DataFrame({"symbol": ["AAA", "BBB"], "close": [12, 8] if day == cfg.report_date else [10, 10], "volume": [1000000, 1000000]})
                def _get(self, *args): return {"results": []}
            sec = Mock()
            sec.fundamentals.return_value = {"revenue": 100, "eps": 2, "shares_outstanding": 100}
            sec.ownership_summary.return_value = {"summary": "test", "filings": []}
            ns = dict(vars(report))
            ns.update(CACHE_DIR=Path(folder), INDEX_SERIES=[], HttpClient=lambda _: object(), MassiveClient=Massive, SecClient=lambda _: sec, FinraClient=lambda *args: Mock(history=Mock(return_value=pd.DataFrame())))
            env = {"MASSIVE_API_KEY": "test", "ALPACA_API_KEY_ID": "test", "ALPACA_API_SECRET_KEY": "test", "SEC_USER_AGENT": "Tests test@example.com"}
            with patch.dict(os.environ, env, clear=True), patch("hybrid_runtime.AlpacaClient") as alpaca, patch("hybrid_runtime.SecBulkClient") as bulk:
                alpaca.return_value.histories.return_value = {}
                bulk.return_value.ticker_map.return_value = {"AAA": {"cik": "01"}, "BBB": {"cik": "02"}}
                bulk.return_value.company_payloads.return_value = {key: {"facts": {"test": 1}, "submissions": {"sicDescription": "Software"}} for key in ["01", "02"]}
                hybrid_runtime.install(ns)
                result = ns["live_payload"](cfg)
                self.assertEqual(len(result["gainers"]), 1)
                self.assertEqual(alpaca.call_args.kwargs["feed"], "iex")
                self.assertTrue(all(call.kwargs.get("as_of") == self.day for call in sec.fundamentals.call_args_list))
                self.assertEqual(sec.fundamentals.call_count, 2)
                self.assertTrue(all(call.kwargs.get("as_of") == self.day for call in sec.ownership_summary.call_args_list))

    def test_external_links_reject_unsafe_protocols(self):
        from news_translation import safe_external_url
        for value in ["javascript:alert(1)", "data:text/html,test", "//example.com", "https://user:password@example.com", "https://example.com/\nunsafe"]:
            with self.subTest(value=value): self.assertEqual(safe_external_url(value), "")
        self.assertEqual(safe_external_url("https://example.com/news?a=1&b=2"), "https://example.com/news?a=1&b=2")

    def test_bilingual_catalog_drops_unsafe_original_link(self):
        with tempfile.TemporaryDirectory() as folder:
            translator = CachedAiTranslator(Path(folder), translate_fn=lambda value: "中文 " + value)
            rows = [{"headline": "News", "summary": "Summary", "created_at": "2026-08-10T10:00:00Z", "url": "javascript:alert(1)"}]
            self.assertEqual(bilingual_news(rows, translator, symbol="AAA")[0]["url"], "")


if __name__ == "__main__": unittest.main()
