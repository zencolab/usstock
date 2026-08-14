from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

# High-confidence translations for frequent SEC SIC labels. Unknown labels fall
# back to the local neural translator.
INDUSTRY_OVERRIDES = {
    "computer communications equipment": "计算机通信设备",
    "insurance agents, brokers & service": "保险代理、经纪及相关服务",
    "semiconductors & related device manufacturing": "半导体及相关器件制造",
    "pharmaceutical preparations": "药物制剂",
    "services-prepackaged software": "预包装软件服务",
    "services-computer programming, data processing, etc.": "计算机编程、数据处理及相关服务",
    "biological products, (no diagnostic substances)": "生物制品（不含诊断用品）",
    "retail-catalog & mail-order houses": "目录及邮购零售",
    "motor vehicles & passenger car bodies": "机动车及乘用车车身制造",
    "crude petroleum & natural gas": "原油与天然气",
    "electric services": "电力服务",
    "real estate investment trusts": "房地产投资信托",
    "state commercial banks": "州级商业银行",
    "national commercial banks": "全国性商业银行",
    "finance services": "金融服务",
    "investment advice": "投资顾问服务",
    "hospital & medical service plans": "医院及医疗服务计划",
}

IMPORTANT_TERMS: tuple[tuple[str, float], ...] = (
    ("bankruptcy", 12),
    ("chapter 11", 12),
    ("fda approval", 11),
    ("fda", 8),
    ("clinical trial", 8),
    ("phase 3", 9),
    ("phase iii", 9),
    ("merger", 10),
    ("acquisition", 9),
    ("acquire", 8),
    ("strategic review", 7),
    ("earnings", 8),
    ("quarterly results", 8),
    ("financial results", 8),
    ("revenue", 5),
    ("guidance", 7),
    ("outlook", 5),
    ("profit warning", 9),
    ("restatement", 10),
    ("sec investigation", 10),
    ("investigation", 6),
    ("lawsuit", 6),
    ("settlement", 5),
    ("public offering", 8),
    ("registered direct", 8),
    ("private placement", 7),
    ("stock offering", 8),
    ("debt offering", 6),
    ("buyback", 6),
    ("share repurchase", 6),
    ("dividend", 5),
    ("contract", 5),
    ("partnership", 4),
    ("ceo", 4),
    ("chief executive", 4),
    ("resigns", 6),
    ("delisting", 9),
    ("nasdaq compliance", 7),
    ("patent", 4),
    ("data breach", 9),
    ("cyberattack", 9),
    ("recall", 8),
)


def _session(headers: dict[str, str]) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(headers)
    return session


class _RequestGate:
    def __init__(self, requests_per_minute: float) -> None:
        self.interval = 60.0 / max(requests_per_minute, 1.0)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            if delay:
                time.sleep(delay)
            self.next_at = max(now, self.next_at) + self.interval


class AlpacaNewsClient:
    """Fetch and cache up to 50 news records per selected symbol."""

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        cache_dir: Path,
        *,
        requests_per_minute: float = 180,
        workers: int = 8,
    ) -> None:
        self.cache_dir = cache_dir / "news"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.workers = max(1, min(workers, 12))
        self.gate = _RequestGate(requests_per_minute)
        self.local = threading.local()
        self.headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
            "User-Agent": "USMarketCloseReport/3.0",
        }

    def _client(self) -> requests.Session:
        client = getattr(self.local, "client", None)
        if client is None:
            client = _session(self.headers)
            self.local.client = client
        return client

    def _cache_file(self, symbol: str, report_date: date) -> Path:
        day_dir = self.cache_dir / report_date.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir / f"{symbol}.json"

    def _fetch_one(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        cache_file = self._cache_file(symbol, end)
        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if isinstance(cached, list):
                    return [row for row in cached if isinstance(row, dict)]
            except Exception:
                pass

        self.gate.wait()
        response = self._client().get(
            ALPACA_NEWS_URL,
            params={
                "symbols": symbol,
                "start": f"{start.isoformat()}T00:00:00Z",
                "end": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
                "sort": "desc",
                "limit": 50,
            },
            timeout=60,
        )
        if response.status_code in {401, 403}:
            raise RuntimeError("Alpaca news access was rejected; check the API credentials and market-data plan")
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("news") or []
        if not isinstance(rows, list):
            rows = []
        normalized = [row for row in rows if isinstance(row, dict)]
        cache_file.write_text(json.dumps(normalized, ensure_ascii=False), encoding="utf-8")
        return normalized

    def news_for_symbols(self, symbols: Iterable[str], start: date, end: date) -> dict[str, list[dict[str, Any]]]:
        wanted = list(dict.fromkeys(str(symbol).upper() for symbol in symbols if symbol))
        result: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in wanted}
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._fetch_one, symbol, start, end): symbol for symbol in wanted}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    result[symbol] = future.result()
                except Exception as exc:
                    print(f"warning: Alpaca news unavailable for {symbol}: {exc}")
        return result


def _plain_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()


def _published_datetime(item: dict[str, Any]) -> datetime | None:
    raw = str(item.get("created_at") or item.get("updated_at") or "")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _importance_score(item: dict[str, Any], report_date: date) -> float:
    headline = _plain_text(item.get("headline"))
    summary = _plain_text(item.get("summary"))
    text = f"{headline} {summary}".lower()
    score = sum(weight for phrase, weight in IMPORTANT_TERMS if phrase in text)
    published = _published_datetime(item)
    if published:
        age_days = max(0, (report_date - published.date()).days)
        score += max(0.0, 3.0 - age_days / 30.0)
    symbols = item.get("symbols") or []
    if isinstance(symbols, list) and 0 < len(symbols) <= 3:
        score += 1.0
    if headline and len(headline) >= 30:
        score += 0.5
    return score


def select_important_news(
    rows: Iterable[dict[str, Any]], report_date: date, *, limit: int = 5
) -> list[dict[str, Any]]:
    ranked: list[tuple[float, datetime, dict[str, Any]]] = []
    seen: set[str] = set()
    cutoff = report_date - timedelta(days=92)
    for row in rows:
        headline = _plain_text(row.get("headline"))
        url = str(row.get("url") or "")
        key = url or headline.lower()
        if not headline or not key or key in seen:
            continue
        published = _published_datetime(row)
        if published and not (cutoff <= published.date() <= report_date + timedelta(days=1)):
            continue
        event_text = f"{headline} {_plain_text(row.get('summary'))}".lower()
        if not any(phrase in event_text for phrase, _ in IMPORTANT_TERMS):
            continue
        score = _importance_score(row, report_date)
        if score < 4.0:
            continue
        seen.add(key)
        ranked.append((score, published or datetime.min.replace(tzinfo=timezone.utc), row))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in ranked[: max(1, limit)]]


class CachedNeuralTranslator:
    """Local en→zh neural translation with a persistent text cache.

    Argos Translate and its model are imported/downloaded only in live mode.
    A callable can be injected for deterministic unit tests.
    """

    def __init__(
        self,
        cache_dir: Path,
        *,
        translate_fn: Callable[[str], str] | None = None,
    ) -> None:
        self.cache_dir = cache_dir / "translation"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "en-zh.json"
        self._translate_fn = translate_fn
        self._model: Any = None
        self._model_error: str | None = None
        self._dirty = 0
        try:
            loaded = json.loads(self.cache_file.read_text(encoding="utf-8")) if self.cache_file.exists() else {}
            self.cache: dict[str, dict[str, str]] = loaded if isinstance(loaded, dict) else {}
        except Exception:
            self.cache = {}

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _load_model(self) -> Any:
        if self._translate_fn is not None:
            return self._translate_fn
        if self._model is not None:
            return self._model
        if self._model_error is not None:
            return None
        try:
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
                available = argos_package.get_available_packages()
                package = next(
                    candidate
                    for candidate in available
                    if candidate.from_code == "en" and candidate.to_code == "zh"
                )
                download_path = package.download()
                argos_package.install_from_path(download_path)
                model = find_model()
            if model is None:
                raise RuntimeError("Argos en→zh model was not found after installation")
            self._model = model
            return model
        except Exception as exc:
            self._model_error = str(exc)
            print(f"warning: local neural translation unavailable: {exc}")
            return None

    def translate(self, text: Any, *, industry: bool = False) -> str:
        source = _plain_text(text)
        if not source:
            return ""
        if industry:
            override = INDUSTRY_OVERRIDES.get(source.lower())
            if override:
                return override
        key = self._key(source)
        cached = self.cache.get(key)
        if isinstance(cached, dict) and cached.get("source") == source and cached.get("translation"):
            return cached["translation"]
        model = self._load_model()
        if model is None:
            return "（自动翻译暂不可用）"
        try:
            translated = model(source) if callable(model) else model.translate(source)
            translated = str(translated or "").strip() or "（自动翻译暂不可用）"
        except Exception as exc:
            print(f"warning: translation failed: {exc}")
            return "（自动翻译暂不可用）"
        self.cache[key] = {"source": source, "translation": translated}
        self._dirty += 1
        if self._dirty >= 25:
            self.flush()
        return translated

    def flush(self) -> None:
        if not self._dirty:
            return
        temp = self.cache_file.with_suffix(".tmp")
        temp.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.cache_file)
        self._dirty = 0


def _summary_paragraphs(item: dict[str, Any], *, max_paragraphs: int = 3) -> list[str]:
    text = _plain_text(item.get("summary"))
    if not text:
        text = _plain_text(item.get("content"))[:1600]
    if not text:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return paragraphs[:max_paragraphs] if paragraphs else [text]


def bilingual_news(
    rows: Iterable[dict[str, Any]],
    report_date: date,
    translator: CachedNeuralTranslator,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in select_important_news(rows, report_date, limit=limit):
        headline_en = _plain_text(item.get("headline"))
        published = _published_datetime(item)
        paragraphs = [
            {"en": paragraph, "zh": translator.translate(paragraph)}
            for paragraph in _summary_paragraphs(item)
        ]
        result.append(
            {
                "headline_en": headline_en,
                "headline_zh": translator.translate(headline_en),
                "published_at": published.date().isoformat() if published else "",
                "source": str(item.get("source") or "Benzinga via Alpaca"),
                "author": str(item.get("author") or ""),
                "url": str(item.get("url") or ""),
                "paragraphs": paragraphs,
            }
        )
    return result
