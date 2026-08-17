from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from src.crawler import clean_text, extract_jsonld_articles
from src.models import NewsItem, canonicalize_url
from src.report import render_html
from src.translator import extract_json_object


class CoreTests(unittest.TestCase):
    def test_canonicalize_url_removes_tracking(self) -> None:
        value = canonicalize_url("https://Example.com/a/?utm_source=x&id=2#section")
        self.assertEqual(value, "https://example.com/a?id=2")

    def test_clean_text_removes_markup(self) -> None:
        self.assertEqual(clean_text("<p>Hello&nbsp; <b>market</b></p>"), "Hello market")

    def test_extract_jsonld_article(self) -> None:
        payload = {
            "@context": "https://schema.org",
            "@type": "NewsArticle",
            "headline": "Stocks rally after economic report",
            "url": "/markets/stocks-rally",
            "description": "A short summary.",
            "datePublished": "2026-08-17T10:00:00Z",
        }
        html = f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        source = {"id": "test", "name": "Test"}
        items = extract_jsonld_articles(html, "https://example.com", source)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://example.com/markets/stocks-rally")

    def test_news_key_is_stable_without_utm(self) -> None:
        first = NewsItem("x", "X", "Same title", "https://example.com/a?utm_source=one")
        second = NewsItem("x", "X", "Same title", "https://example.com/a?utm_source=two")
        self.assertEqual(first.key, second.key)

    def test_extract_json_from_code_fence(self) -> None:
        result = extract_json_object('```json\n{"items": []}\n```')
        self.assertEqual(result, {"items": []})

    def test_html_report_is_bilingual_and_escapes_content(self) -> None:
        item = NewsItem(
            "test",
            "Test & Wire",
            "Markets <rise>",
            "https://example.com/news?id=1&lang=en",
            "English summary",
            datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
            "市场上涨",
            "中文摘要",
        )
        html = render_html(
            [item],
            generated_at=datetime(2026, 8, 17, 11, 0, tzinfo=UTC),
            timezone_name="UTC",
            source_counts={"Test & Wire": 1},
            errors=[],
        )
        self.assertIn("市场上涨", html)
        self.assertIn("Markets &lt;rise&gt;", html)
        self.assertIn("id=1&amp;lang=en", html)
        self.assertIn('<meta name="viewport"', html)


if __name__ == "__main__":
    unittest.main()
