"""Regression tests for the confirmed code-review findings.

Covers:
  #1 Jinja2 autoescape did not match the ``*.html.j2`` template suffix, and
     link targets were never protocol-checked.
  #2 Historical backfills used the latest available SEC disclosure and
     ``date.today()`` instead of the report date.
  #4 ``ALPACA_FEED`` defaulted to ``sip`` locally but ``iex`` in the workflows.

All tests are offline: no HTTP, no Drive upload, no Pages publish.
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import report_asof
from runtime_config import DEFAULT_ALPACA_FEED, resolve_alpaca_feed
from template_env import BLOCKED_URL_PLACEHOLDER, build_environment, safe_external_url

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"


class TemplateAutoescapeTests(unittest.TestCase):
    """Review issue #1."""

    def setUp(self) -> None:
        self.env = build_environment(TEMPLATES)

    def test_environment_autoescapes_html_j2_templates(self) -> None:
        # select_autoescape(["html", "xml"]) returned False here; that was the bug.
        self.assertTrue(self.env.autoescape)
        template = self.env.from_string("{{ value }}")
        self.assertEqual(template.render(value="<script>x</script>"), "&lt;script&gt;x&lt;/script&gt;")

    def test_news_template_escapes_model_and_source_text(self) -> None:
        html = self.env.get_template("news.html.j2").render(
            stock={"symbol": "ABC", "name": "Alpha & Co", "file_name": "abc"},
            report_date="2026-09-08",
            article={
                "headline_zh": "<img src=x onerror=alert(1)>",
                "headline_en": "Markets <rise>",
                "url": "https://example.com/a?id=1&lang=en",
                "paragraphs": [{"en": "<b>bold</b>", "zh": "</p><script>alert(2)</script>"}],
            },
        )
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertNotIn("<script>alert(2)</script>", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertIn("Markets &lt;rise&gt;", html)
        self.assertIn("id=1&amp;lang=en", html)

    def test_news_template_neutralises_javascript_links(self) -> None:
        html = self.env.get_template("news.html.j2").render(
            stock={"symbol": "ABC", "name": "Alpha", "file_name": "abc"},
            report_date="2026-09-08",
            article={
                "headline_zh": "x",
                "headline_en": "x",
                "url": "javascript:alert(document.cookie)",
                "paragraphs": [],
            },
        )
        self.assertNotIn("javascript:", html)
        self.assertIn(f'href="{BLOCKED_URL_PLACEHOLDER}"', html)

    def test_safe_external_url_allows_normal_links(self) -> None:
        self.assertEqual(safe_external_url("https://example.com/a"), "https://example.com/a")
        self.assertEqual(safe_external_url("http://example.com"), "http://example.com")
        self.assertEqual(safe_external_url("../index.html"), "../index.html")

    def test_safe_external_url_blocks_dangerous_schemes(self) -> None:
        for value in (
            "javascript:alert(1)",
            "JavaScript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "vbscript:msgbox(1)",
            "java\tscript:alert(1)",
            "//evil.example/x",
            "",
            None,
        ):
            with self.subTest(value=value):
                self.assertEqual(safe_external_url(value), BLOCKED_URL_PLACEHOLDER)

    def test_custom_filters_are_still_registered(self) -> None:
        env = build_environment(TEMPLATES, filters={"pct": lambda v: f"{v:+.2f}%"})
        self.assertEqual(env.from_string("{{ 1.5|pct }}").render(), "+1.50%")
        self.assertIn("safe_external_url", env.filters)


COMPANY_FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "val": 100.0,
                            "form": "10-K",
                            "start": "2024-01-01",
                            "end": "2024-12-31",
                            "filed": "2025-02-20",
                        },
                        {
                            "val": 250.0,
                            "form": "10-K",
                            "start": "2025-01-01",
                            "end": "2025-12-31",
                            "filed": "2026-02-20",
                        },
                    ]
                }
            },
            "EarningsPerShareDiluted": {
                "units": {
                    "USD/shares": [
                        {
                            "val": 1.0,
                            "form": "10-K",
                            "start": "2024-01-01",
                            "end": "2024-12-31",
                            "filed": "2025-02-20",
                        },
                        {
                            "val": 3.0,
                            "form": "10-K",
                            "start": "2025-01-01",
                            "end": "2025-12-31",
                            "filed": "2026-02-20",
                        },
                    ]
                }
            },
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {"val": 1000.0, "end": "2025-01-31", "filed": "2025-02-20"},
                        {"val": 4000.0, "end": "2026-01-31", "filed": "2026-02-20"},
                    ]
                }
            }
        },
    }
}

SUBMISSIONS = {
    "cik": 320193,
    "filings": {
        "recent": {
            "form": ["4", "SC 13G/A", "4", "10-Q", "SC 13D"],
            "filingDate": [
                "2026-03-01",  # after a 2025-06-30 report date
                "2025-06-10",
                "2025-06-20",
                "2025-06-25",
                "2024-01-05",  # older than the 365-day lookback
            ],
            "accessionNumber": [
                "0000320193-26-000001",
                "0000320193-25-000002",
                "0000320193-25-000003",
                "0000320193-25-000004",
                "0000320193-24-000005",
            ],
            "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm", "e.htm"],
        }
    },
}

REPORT_DATE = date(2025, 6, 30)


class PointInTimeFundamentalsTests(unittest.TestCase):
    """Review issue #2."""

    def test_fundamentals_ignore_disclosures_filed_after_report_date(self) -> None:
        values = report_asof.fundamentals(COMPANY_FACTS, as_of=REPORT_DATE)
        self.assertEqual(values["revenue"], 100.0)
        self.assertEqual(values["eps"], 1.0)
        self.assertEqual(values["shares_outstanding"], 1000.0)

    def test_fundamentals_without_as_of_keep_latest_available(self) -> None:
        values = report_asof.fundamentals(COMPANY_FACTS)
        self.assertEqual(values["revenue"], 250.0)
        self.assertEqual(values["eps"], 3.0)
        self.assertEqual(values["shares_outstanding"], 4000.0)

    def test_annual_fact_rejects_non_annual_durations(self) -> None:
        quarterly = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "USD": [
                                {
                                    "val": 10.0,
                                    "form": "10-K",
                                    "start": "2025-01-01",
                                    "end": "2025-03-31",
                                    "filed": "2025-04-10",
                                }
                            ]
                        }
                    }
                }
            }
        }
        self.assertIsNone(
            report_asof.latest_annual_fact(
                quarterly, report_asof.REVENUE_TAGS, ["USD"], as_of=date(2026, 1, 1)
            )
        )

    def test_facts_without_a_usable_date_are_excluded_when_as_of_is_set(self) -> None:
        undated = {
            "facts": {
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {"shares": [{"val": 5.0}]}
                    }
                }
            }
        }
        self.assertIsNone(
            report_asof.latest_instant_fact(
                undated, report_asof.SHARES_TAGS, ["shares"], as_of=REPORT_DATE
            )
        )
        self.assertEqual(
            report_asof.latest_instant_fact(undated, report_asof.SHARES_TAGS, ["shares"]),
            5.0,
        )

    def test_coerce_date_handles_sec_formats_and_junk(self) -> None:
        self.assertEqual(report_asof.coerce_date("2025-06-30"), date(2025, 6, 30))
        self.assertEqual(report_asof.coerce_date("2025-06-30T00:00:00Z"), date(2025, 6, 30))
        self.assertIsNone(report_asof.coerce_date("not-a-date"))
        self.assertIsNone(report_asof.coerce_date(None))


class PointInTimeOwnershipTests(unittest.TestCase):
    """Review issue #2, insider window."""

    def test_filings_after_the_report_date_are_dropped(self) -> None:
        summary = report_asof.ownership_summary(SUBMISSIONS, 320193, as_of=REPORT_DATE)
        dates = [row["filing_date"] for row in summary["filings"]]
        self.assertNotIn("2026-03-01", dates)
        self.assertTrue(all(row <= REPORT_DATE.isoformat() for row in dates))

    def test_lookback_window_is_measured_from_the_report_date(self) -> None:
        summary = report_asof.ownership_summary(SUBMISSIONS, 320193, as_of=REPORT_DATE)
        dates = [row["filing_date"] for row in summary["filings"]]
        self.assertNotIn("2024-01-05", dates)
        self.assertEqual(dates, ["2025-06-20", "2025-06-10"])

    def test_insider_window_does_not_use_the_run_date(self) -> None:
        summary = report_asof.ownership_summary(SUBMISSIONS, 320193, as_of=REPORT_DATE)
        # 2025-06-20 is within 90 days of the report date, not of "today".
        self.assertEqual(summary["insider_count"], 1)
        self.assertEqual(summary["beneficial_count"], 1)
        self.assertEqual(summary["as_of"], REPORT_DATE.isoformat())

    def test_summary_text_states_the_as_of_date(self) -> None:
        summary = report_asof.ownership_summary(SUBMISSIONS, 320193, as_of=REPORT_DATE)
        self.assertIn(REPORT_DATE.isoformat(), summary["summary"])

    def test_filing_urls_point_at_sec_archives(self) -> None:
        summary = report_asof.ownership_summary(SUBMISSIONS, 320193, as_of=REPORT_DATE)
        for row in summary["filings"]:
            self.assertTrue(row["url"].startswith("https://www.sec.gov/Archives/edgar/data/320193/"))

    def test_no_matching_filings_returns_an_explicit_summary(self) -> None:
        summary = report_asof.ownership_summary(
            {"cik": 1, "filings": {"recent": {"form": ["10-Q"], "filingDate": ["2025-01-01"]}}},
            1,
            as_of=REPORT_DATE,
        )
        self.assertEqual(summary["filings"], [])
        self.assertIn(REPORT_DATE.isoformat(), summary["summary"])


class AlpacaFeedDefaultTests(unittest.TestCase):
    """Review issue #4."""

    def test_default_matches_the_workflow_default(self) -> None:
        self.assertEqual(DEFAULT_ALPACA_FEED, "iex")
        self.assertEqual(resolve_alpaca_feed({}), "iex")

    def test_blank_value_falls_back_to_the_shared_default(self) -> None:
        self.assertEqual(resolve_alpaca_feed({"ALPACA_FEED": "   "}), "iex")

    def test_explicit_value_is_normalised(self) -> None:
        self.assertEqual(resolve_alpaca_feed({"ALPACA_FEED": " SIP "}), "sip")

    def test_unknown_feed_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            resolve_alpaca_feed({"ALPACA_FEED": "nasdaq-basic"})


if __name__ == "__main__":
    unittest.main()
