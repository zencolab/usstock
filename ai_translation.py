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


def _session(headers: dict[str, str] | None = None) -> requests.Session:
    client = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=1.2,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        allowed_methods=("POST",),
        respect_retry_after_header=True,
    )
    client.mount("https://", HTTPAdapter(max_retries=retry))
    client.mount("http://", HTTPAdapter(max_retries=retry))
    if headers:
        client.headers.update(headers)
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


class GeminiBackend(TranslationBackend):
    provider = "Google Gemini"

    def __init__(self, api_key: str, model: str, requests_per_minute: float) -> None:
        if not api_key:
            raise RuntimeError("TRANSLATION_PROVIDER=gemini requires the GEMINI_API_KEY Repository secret")
        self.model = model
        self.gate = _Gate(requests_per_minute)
        self.client = _session({"x-goog-api-key": api_key, "Content-Type": "application/json"})
        self.url = "https://" + f"generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def translate_many(self, texts: list[str], *, industry: bool = False) -> list[str]:
        if not texts:
            return []
        prompt = INDUSTRY_PROMPT if industry else SYSTEM_PROMPT
        body = {
            "systemInstruction": {"parts": [{"text": prompt}]},
            "contents": [{"role": "user", "parts": [{"text": json.dumps({"source_language": "English", "target_language": "Simplified Chinese", "texts": texts}, ensure_ascii=False)}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }
        self.gate.wait()
        response = self.client.post(self.url, json=body, timeout=180)
        if response.status_code in {400, 401, 403, 404}:
            message = response.text[:500].replace("\n", " ")
            raise RuntimeError(f"Gemini translation request rejected ({response.status_code}): {message}")
        response.raise_for_status()
        payload = response.json()
        try:
            parts = payload["candidates"][0]["content"]["parts"]
            text = "".join(str(part.get("text") or "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Gemini translation response did not contain text") from exc
        return _decode_json(text, len(texts))


class OllamaBackend(TranslationBackend):
    provider = "Ollama"

    def __init__(self, base_url: str, api_key: str, model: str, requests_per_minute: float) -> None:
        self.model = model
        root = (base_url or "http://localhost:11434").rstrip("/")
        if not root.endswith("/api"):
            root += "/api"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = _session(headers)
        self.url = f"{root}/chat"
        self.gate = _Gate(requests_per_minute)

    def translate_many(self, texts: list[str], *, industry: bool = False) -> list[str]:
        if not texts:
            return []
        prompt = INDUSTRY_PROMPT if industry else SYSTEM_PROMPT
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps({"source_language": "English", "target_language": "Simplified Chinese", "texts": texts}, ensure_ascii=False)},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1},
        }
        self.gate.wait()
        response = self.client.post(self.url, json=body, timeout=600)
        if response.status_code in {400, 401, 403, 404}:
            message = response.text[:500].replace("\n", " ")
            raise RuntimeError(f"Ollama translation request rejected ({response.status_code}): {message}")
        response.raise_for_status()
        payload = response.json()
        text = str((payload.get("message") or {}).get("content") or payload.get("response") or "")
        if not text:
            raise RuntimeError("Ollama translation response did not contain text")
        return _decode_json(text, len(texts))


class ArgosBackend(TranslationBackend):
    provider = "Argos"
    model = "en-zh-local-neural"

    def __init__(self) -> None:
        self._model: Any = None

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        import argostranslate.package as argos_package
        import argostranslate.translate as argos_translate

        def find_model() -> Any:
            languages = argos_translate.get_installed_languages()
            source = next((language for language in languages if language.code == "en"), None)
            target = next((language for language in languages if language.code == "zh"), None)
            return source.get_translation(target) if source and target else None

        model = find_model()
        if model is None:
            print("Downloading the Argos English-to-Chinese neural translation model …")
            argos_package.update_package_index()
            package = next(candidate for candidate in argos_package.get_available_packages() if candidate.from_code == "en" and candidate.to_code == "zh")
            argos_package.install_from_path(package.download())
            model = find_model()
        if model is None:
            raise RuntimeError("Argos en→zh model was not found after installation")
        self._model = model
        return model

    def translate_many(self, texts: list[str], *, industry: bool = False) -> list[str]:
        model = self._load_model()
        return [str(model.translate(text) or "").strip() for text in texts]


def build_translation_backend(*, translate_fn: Callable[[str], str] | None = None) -> TranslationBackend:
    if translate_fn is not None:
        return CallableBackend(translate_fn)

    requested = os.getenv("TRANSLATION_PROVIDER", "auto").strip().lower() or "auto"
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    ollama_base_url = os.getenv("OLLAMA_BASE_URL", "").strip()
    ollama_key = os.getenv("OLLAMA_API_KEY", "").strip()

    if requested == "auto":
        requested = "gemini" if gemini_key else ("ollama" if ollama_base_url else "argos")
    if requested == "gemini":
        return GeminiBackend(gemini_key, os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash", float(os.getenv("GEMINI_RPM", "10")))
    if requested == "ollama":
        return OllamaBackend(ollama_base_url, ollama_key, os.getenv("OLLAMA_MODEL", "gemma4").strip() or "gemma4", float(os.getenv("OLLAMA_RPM", "20")))
    if requested == "argos":
        return ArgosBackend()
    raise RuntimeError("TRANSLATION_PROVIDER must be auto, gemini, ollama, or argos")
