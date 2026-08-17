from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import NewsItem

LOGGER = logging.getLogger(__name__)


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```")
        stripped = stripped.removesuffix("```").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Model response did not contain a JSON object")
    return json.loads(stripped[start : end + 1])


class OllamaTranslator:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = "gemma4:cloud",
        timeout_seconds: float = 120,
    ) -> None:
        if not api_key:
            raise ValueError("OLLAMA_API_KEY is required")
        clean_base = (base_url or "https://ollama.com").rstrip("/")
        self.chat_url = f"{clean_base}/chat" if clean_base.endswith("/api") else f"{clean_base}/api/chat"
        self.model = model or "gemma4:cloud"
        self.headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self.timeout_seconds = timeout_seconds

    def close(self) -> None:
        return None

    def _chat(self, prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a precise financial-news translator. Translate English into Simplified "
                        "Chinese. Preserve company names, ticker symbols, numbers, currencies, dates and "
                        "uncertainty. Do not add facts, advice or commentary. Return JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        request = Request(
            self.chat_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=self.headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc
        content = (data.get("message") or {}).get("content")
        if content is None and data.get("choices"):
            content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise ValueError("Unexpected Ollama response shape")
        return extract_json_object(content)

    def _translate_batch(self, items: list[NewsItem]) -> None:
        request_items = [
            {
                "id": str(index),
                "title_en": item.title_en,
                "summary_en": item.summary_en,
            }
            for index, item in enumerate(items)
        ]
        prompt = (
            "Translate every title_en and summary_en. Keep summary_zh empty when summary_en is empty. "
            "Return exactly this shape: "
            '{"items":[{"id":"0","title_zh":"...","summary_zh":"..."}]}.\n\n'
            + json.dumps({"items": request_items}, ensure_ascii=False)
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                translated = self._chat(prompt)
                rows = translated.get("items")
                if not isinstance(rows, list):
                    raise ValueError("Translation JSON is missing items[]")
                by_id = {str(row.get("id")): row for row in rows if isinstance(row, dict)}
                for index, item in enumerate(items):
                    row = by_id.get(str(index), {})
                    item.title_zh = str(row.get("title_zh") or "").strip()
                    item.summary_zh = str(row.get("summary_zh") or "").strip()
                    if not item.title_zh:
                        raise ValueError(f"Missing title_zh for item {index}")
                return
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                LOGGER.warning("Translation attempt %s failed: %s", attempt + 1, exc)
                time.sleep(2**attempt)
        raise RuntimeError(f"Translation failed after 3 attempts: {last_error}")

    def translate(self, items: list[NewsItem], batch_size: int = 8) -> list[str]:
        errors: list[str] = []
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            try:
                self._translate_batch(batch)
            except Exception as exc:
                errors.append(f"Items {start + 1}-{start + len(batch)}: {exc}")
                for item in batch:
                    item.title_zh = item.title_zh or "（翻译暂不可用）"
                    item.summary_zh = item.summary_zh or ""
        return errors
