from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from ai_translation import build_translation_backend
from news_translation import (
    INDUSTRY_OVERRIDES,
    _plain_text,
    _published_datetime,
    _summary_paragraphs,
)


class CachedAiTranslator:
    """Cached provider-neutral English-to-Chinese AI translation."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        translate_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.backend = build_translation_backend(translate_fn=translate_fn)
        self.cache_dir = cache_dir / "translation"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / f"{self.backend.cache_namespace}-en-zh.json"
        self._dirty = 0
        try:
            loaded = (
                json.loads(self.cache_file.read_text(encoding="utf-8"))
                if self.cache_file.exists()
                else {}
            )
            self.cache: dict[str, dict[str, str]] = loaded if isinstance(loaded, dict) else {}
        except Exception:
            self.cache = {}

    @property
    def source_label(self) -> str:
        return self.backend.source_label

    @staticmethod
    def _key(text: str, industry: bool) -> str:
        context = "industry" if industry else "financial-news"
        return hashlib.sha256(f"{context}\0{text}".encode("utf-8")).hexdigest()

    def translate_many(self, texts: Iterable[Any], *, industry: bool = False) -> list[str]:
        sources = [_plain_text(text) for text in texts]
        translations: dict[str, str] = {}
        missing: list[str] = []
        for source in sources:
            if not source:
                translations[source] = ""
                continue
            if industry:
                override = INDUSTRY_OVERRIDES.get(source.lower())
                if override:
                    translations[source] = override
                    continue
            key = self._key(source, industry)
            cached = self.cache.get(key)
            if (
                isinstance(cached, dict)
                and cached.get("source") == source
                and cached.get("translation")
            ):
                translations[source] = cached["translation"]
            elif source not in missing:
                missing.append(source)

        batch_size = 20
        for offset in range(0, len(missing), batch_size):
            batch = missing[offset : offset + batch_size]
            translated = self.backend.translate_many(batch, industry=industry)
            if len(translated) != len(batch):
                raise RuntimeError("AI translation response count mismatch")
            for source, target in zip(batch, translated):
                target = str(target or "").strip()
                if not target:
                    raise RuntimeError("AI translation returned an empty result")
                translations[source] = target
                self.cache[self._key(source, industry)] = {
                    "source": source,
                    "translation": target,
                }
                self._dirty += 1
            if self._dirty >= 20:
                self.flush()
        return [translations[source] for source in sources]

    def translate(self, text: Any, *, industry: bool = False) -> str:
        return self.translate_many([text], industry=industry)[0]

    def flush(self) -> None:
        if not self._dirty:
            return
        temp = self.cache_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.cache_file)
        self._dirty = 0


def _detail_file(symbol: str, item: dict[str, Any], headline: str) -> str:
    published = _published_datetime(item)
    day = published.date().isoformat() if published else "undated"
    identity = str(item.get("url") or item.get("id") or f"{day}|{headline}")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    safe_symbol = re.sub(r"[^A-Z0-9.-]+", "-", symbol.upper()).strip("-") or "STOCK"
    return f"{safe_symbol}-{day}-{digest}.html"


def bilingual_news(
    rows: Iterable[dict[str, Any]],
    translator: CachedAiTranslator,
    *,
    symbol: str,
) -> list[dict[str, Any]]:
    """Translate every catalog headline and available summary paragraph."""
    result: list[dict[str, Any]] = []
    for item in rows:
        headline_en = _plain_text(item.get("headline"))
        if not headline_en:
            continue
        published = _published_datetime(item)
        paragraph_sources = _summary_paragraphs(item)
        translated = translator.translate_many([headline_en, *paragraph_sources])
        detail_file = _detail_file(symbol, item, headline_en)
        paragraphs = [
            {"en": paragraph, "zh": target}
            for paragraph, target in zip(paragraph_sources, translated[1:])
        ]
        result.append(
            {
                "headline_en": headline_en,
                "headline_zh": translated[0],
                "published_at": published.date().isoformat() if published else "",
                "published_iso": published.isoformat() if published else "",
                "source": str(item.get("source") or "Benzinga via Alpaca"),
                "author": str(item.get("author") or ""),
                "url": str(item.get("url") or ""),
                "detail_file": detail_file,
                "detail_url": f"../news/{detail_file}",
                "paragraphs": paragraphs,
            }
        )
    result.sort(key=lambda item: item.get("published_iso") or "", reverse=True)
    return result


def collect_news_sources(
    news_by_symbol: dict[str, Iterable[dict[str, Any]]],
) -> list[str]:
    """Collect every unique title and summary so cloud translation is batched."""
    sources: list[str] = []
    seen: set[str] = set()
    for rows in news_by_symbol.values():
        for item in rows:
            values = [_plain_text(item.get("headline")), *_summary_paragraphs(item)]
            for value in values:
                if value and value not in seen:
                    seen.add(value)
                    sources.append(value)
    return sources
