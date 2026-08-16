from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

from ai_translation import OllamaBackend
from news_translation import (
    AlpacaNewsClient,
    calendar_months_before,
    news_window,
    prepare_news_catalog,
)
from premium_translation import CachedAiTranslator, bilingual_news, collect_news_sources


class FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any], text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeGetSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, _url: str, *, params: dict[str, Any], timeout: int) -> FakeResponse:
        self.calls.append(dict(params))
        if len(self.calls) == 1:
            return FakeResponse(
                200,
                {
                    "news": [
                        {
                            "id": 1,
                            "headline": "First page",
                            "created_at": "2026-08-14T12:00:00Z",
                            "symbols": ["ABC"],
                        }
                    ],
                    "next_page_token": "page-2",
                },
            )
        return FakeResponse(
            200,
            {
                "news": [
                    {
                        "id": 2,
                        "headline": "Second page",
                        "created_at": "2026-07-01T12:00:00Z",
                        "symbols": ["ABC"],
                    }
                ]
            },
        )


class FakePostSession:
    def __init__(self) -> None:
        self.headers_seen: list[str] = []

    def post(
        self,
        _url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, str],
        timeout: int,
    ) -> FakeResponse:
        self.headers_seen.append(headers.get("Authorization", ""))
        if len(self.headers_seen) == 1:
            return FakeResponse(429, {"error": "usage limit"})
        return FakeResponse(
            200,
            {"message": {"content": '{"translations":["中文标题"]}'}},
        )


class NewsCatalogTests(unittest.TestCase):
    def test_three_calendar_month_window(self) -> None:
        self.assertEqual(news_window(date(2026, 8, 14)), (date(2026, 5, 14), date(2026, 8, 14)))
        self.assertEqual(calendar_months_before(date(2024, 5, 31), 3), date(2024, 2, 29))
        self.assertEqual(calendar_months_before(date(2025, 5, 31), 3), date(2025, 2, 28))

    def test_catalog_keeps_all_in_window_titles_without_keyword_filter(self) -> None:
        rows = [
            {"id": 1, "headline": "Ordinary product update", "created_at": "2026-05-14T00:00:00Z", "symbols": ["ABC"]},
            {"id": 2, "headline": "Latest interview", "created_at": "2026-08-14T23:59:59Z", "symbols": ["ABC"]},
            {"id": 3, "headline": "Too old", "created_at": "2026-05-13T23:59:59Z", "symbols": ["ABC"]},
            {"id": 4, "headline": "Next day", "created_at": "2026-08-15T00:00:00Z", "symbols": ["ABC"]},
            {"id": 5, "headline": "Other company", "created_at": "2026-08-10T00:00:00Z", "symbols": ["XYZ"]},
            {"id": 2, "headline": "Latest interview", "created_at": "2026-08-14T23:59:59Z", "symbols": ["ABC"]},
        ]
        selected = prepare_news_catalog(rows, "ABC", date(2026, 8, 14))
        self.assertEqual([row["id"] for row in selected], [2, 1])

    def test_alpaca_pagination_and_versioned_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            client = AlpacaNewsClient(
                "key",
                "secret",
                Path(folder),
                requests_per_minute=1_000_000,
                workers=1,
            )
            fake = FakeGetSession()
            client.local.client = fake
            rows = client._fetch_one("ABC", date(2026, 5, 14), date(2026, 8, 14))
            self.assertEqual([row["id"] for row in rows], [1, 2])
            self.assertEqual(len(fake.calls), 2)
            self.assertNotIn("page_token", fake.calls[0])
            self.assertEqual(fake.calls[1]["page_token"], "page-2")
            cached = client._fetch_one("ABC", date(2026, 5, 14), date(2026, 8, 14))
            self.assertEqual(cached, rows)
            self.assertEqual(len(fake.calls), 2)

    def test_every_catalog_title_and_summary_is_translated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            translator = CachedAiTranslator(
                Path(folder), translate_fn=lambda text: f"中：{text}"
            )
            rows = [
                {
                    "id": 10,
                    "headline": "Company launches product",
                    "summary": "The company launched a new product.",
                    "created_at": "2026-08-10T12:00:00Z",
                    "symbols": ["ABC"],
                    "url": "https://example.com/article",
                }
            ]
            sources = collect_news_sources({"ABC": rows})
            self.assertEqual(len(sources), 2)
            translated = bilingual_news(rows, translator, symbol="ABC")
            self.assertEqual(translated[0]["headline_zh"], "中：Company launches product")
            self.assertEqual(translated[0]["paragraphs"][0]["zh"], "中：The company launched a new product.")
            self.assertTrue(translated[0]["detail_file"].startswith("ABC-2026-08-10-"))
            self.assertTrue(translated[0]["detail_url"].startswith("../news/"))

    def test_ollama_fallback_credential_on_quota_response(self) -> None:
        backend = OllamaBackend(
            "https://ollama.com/api",
            "primary-token",
            "gemma4:cloud",
            1_000_000,
            fallback_api_keys=["secondary-token"],
        )
        fake = FakePostSession()
        backend.client = fake  # type: ignore[assignment]
        self.assertEqual(backend.translate_many(["Headline"]), ["中文标题"])
        self.assertEqual(
            fake.headers_seen,
            ["Bearer primary-token", "Bearer secondary-token"],
        )


if __name__ == "__main__":
    unittest.main()
