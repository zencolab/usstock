from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SYSTEM_PROMPT = """You are a professional English-to-Simplified-Chinese translator specializing in US equities, SEC terminology, earnings, M&A, regulation, financing, and corporate news.
Translate every input item faithfully and completely. Do not summarize, omit, explain, or add investment opinions.
Preserve company names, product names, stock tickers, exchange codes, dates, percentages, EPS, and all numeric facts. Translate currency units accurately; for example, $4 billion is 40亿美元, not 4亿元.
Use established mainland-Chinese financial terminology and natural Chinese sentence structure.
Return only one JSON object in this exact shape: {"translations":["..."]}. The array must have exactly the same number of items and the same order as the input array."""

INDUSTRY_PROMPT = """You are a professional SEC industry-classification translator.
Translate each English industry label into a short, standard Simplified-Chinese industry name. Preserve company-specific names and do not add explanations.
Return only one JSON object in this exact shape: {"translations":["..."]}. The array must have exactly the same number of items and the same order as the input array."""


def _session() -> requests.Session:
    client = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.2,
        # 429 is handled by OllamaBackend so a configured fallback token can be tried.
        status_forcelist=(408, 500, 502, 503, 504),
        allowed_methods=("POST",),
        respect_retry_after_header=True,
    )
    client.mount("https://", HTTPAdapter(max_retries=retry))
    client.mount("http://", HTTPAdapter(max_retries=retry))
    client.headers.update({"Content-Type": "application/json"})
    return client


class _Gate:
    def __init__(self, requests_per_minute: float) -> None:
        self.interval = 60.0 / max(requests_per_minute, 1.0)
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            if delay:
                time.sleep(delay)
            self.next_at = max(now, self.next_at) + self.interval


def _decode_json(text: str, expected: int) -> list[str]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    payload = json.loads(cleaned)
    values = payload.get("translations") if isinstance(payload, dict) else payload
    if not isinstance(values, list) or len(values) != expected:
        raise RuntimeError(f"translation response count mismatch: expected {expected}")
    result = [str(value or "").strip() for value in values]
    if any(not value for value in result):
        raise RuntimeError("translation response contains an empty item")
    return result


class TranslationBackend:
    provider = "unknown"
    model = "unknown"

    @property
    def cache_namespace(self) -> str:
        safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", f"{self.provider}-{self.model}")
        return safe.strip("-").lower()

    @property
    def source_label(self) -> str:
        return f"{self.provider} AI translation ({self.model}, English to Chinese)"

    def translate_many(self, texts: list[str], *, industry: bool = False) -> list[str]:
        raise NotImplementedError


class CallableBackend(TranslationBackend):
    provider = "test"
    model = "callable"

    def __init__(self, function: Callable[[str], str]) -> None:
        self.function = function

    def translate_many(self, texts: list[str], *, industry: bool = False) -> list[str]:
        return [str(self.function(text)).strip() for text in texts]


class OllamaBackend(TranslationBackend):
    provider = "Ollama"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        requests_per_minute: float,
        *,
        fallback_api_keys: list[str] | None = None,
    ) -> None:
        self.model = model
        root = (base_url or "https://ollama.com/api").rstrip("/")
        if not root.endswith("/api"):
            root += "/api"
        self.client = _session()
        self.url = f"{root}/chat"
        self.gate = _Gate(requests_per_minute)
        keys = [api_key, *(fallback_api_keys or [])]
        self.api_keys = list(dict.fromkeys(key.strip() for key in keys if key and key.strip()))
        self._active_key = 0

    def _post(self, body: dict[str, Any]) -> requests.Response:
        attempts = max(1, len(self.api_keys))
        last_response: requests.Response | None = None
        for offset in range(attempts):
            key_index = (self._active_key + offset) % attempts if self.api_keys else 0
            headers: dict[str, str] = {}
            if self.api_keys:
                headers["Authorization"] = f"Bearer {self.api_keys[key_index]}"
            self.gate.wait()
            response = self.client.post(self.url, json=body, headers=headers, timeout=600)
            last_response = response
            if response.status_code in {401, 402, 403, 429} and offset + 1 < attempts:
                print(
                    "warning: Ollama credential unavailable or quota-limited; "
                    "trying the configured fallback credential"
                )
                continue
            if self.api_keys:
                self._active_key = key_index
            return response
        if last_response is None:
            raise RuntimeError("Ollama request did not produce a response")
        return last_response

    def translate_many(self, texts: list[str], *, industry: bool = False) -> list[str]:
        if not texts:
            return []
        prompt = INDUSTRY_PROMPT if industry else SYSTEM_PROMPT
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "source_language": "English",
                            "target_language": "Simplified Chinese",
                            "texts": texts,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        response = self._post(body)
        if response.status_code in {400, 401, 402, 403, 404, 429}:
            message = response.text[:500].replace("\n", " ")
            raise RuntimeError(
                f"Ollama translation request rejected ({response.status_code}): {message}"
            )
        response.raise_for_status()
        payload = response.json()
        text = str(
            (payload.get("message") or {}).get("content")
            or payload.get("response")
            or ""
        )
        if not text:
            raise RuntimeError("Ollama translation response did not contain text")
        return _decode_json(text, len(texts))


def build_translation_backend(
    *, translate_fn: Callable[[str], str] | None = None
) -> TranslationBackend:
    if translate_fn is not None:
        return CallableBackend(translate_fn)

    requested = os.getenv("TRANSLATION_PROVIDER", "ollama").strip().lower() or "ollama"
    if requested not in {"auto", "ollama"}:
        raise RuntimeError("This deployment supports TRANSLATION_PROVIDER=ollama only")
    fallback_raw = os.getenv("OLLAMA_API_KEY_FALLBACK", "")
    fallback_keys = [
        value.strip()
        for value in re.split(r"[,;\n]+", fallback_raw)
        if value.strip()
    ]
    return OllamaBackend(
        os.getenv("OLLAMA_BASE_URL", "https://ollama.com/api").strip(),
        os.getenv("OLLAMA_API_KEY", "").strip(),
        os.getenv("OLLAMA_MODEL", "gemma4:cloud").strip() or "gemma4:cloud",
        float(os.getenv("OLLAMA_RPM", "20")),
        fallback_api_keys=fallback_keys,
    )
