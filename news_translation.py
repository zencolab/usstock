from __future__ import annotations

import calendar
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
NEWS_CACHE_VERSION = 2

# High-confidence translations for frequent SEC SIC labels. Unknown labels are
# translated by the configured AI backend.
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


def calendar_months_before(value: date, months: int) -> date:
    """Subtract calendar months, clamping the day at the target month end."""
    if months < 0:
        raise ValueError("months must be non-negative")
    month_index = value.year * 12 + value.month - 1 - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def news_window(report_date: date) -> tuple[date, date]:
    """Return the inclusive three-calendar-month news window."""
    return calendar_months_before(report_date, 3), report_date


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
    """Fetch every Alpaca/Benzinga news page for each selected symbol."""

    def __init__(
        self,
        key_id: str,
        secret_key: str,
        cache_dir: Path,
        *,
        requests_per_minute: float = 180,
        workers: int = 8,
        max_pages: int = 100,
        strict: bool = True,
    ) -> None:
        self.cache_dir = cache_dir / "news"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.workers = max(1, min(workers, 12))
        self.max_pages = max(1, max_pages)
        self.strict = strict
        self.gate = _RequestGate(requests_per_minute)
        self.local = threading.local()
        self.headers = {
            "APCA-API-KEY-ID": key_id,
            "APCA-API-SECRET-KEY": secret_key,
            "Accept": "application/json",
            "User-Agent": "USMarketCloseReport/4.0",
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

    @staticmethod
    def _cached_rows(payload: Any, symbol: str, start: date, end: date) -> list[dict[str, Any]] | None:
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != NEWS_CACHE_VERSION or payload.get("complete") is not True:
            return None
        if payload.get("symbol") != symbol or payload.get("start") != start.isoformat() or payload.get("end") != end.isoformat():
            return None
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return None
        return [row for row in rows if isinstance(row, dict)]

    def _fetch_one(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        cache_file = self._cache_file(symbol, end)
        if cache_file.exists():
            try:
                cached = self._cached_rows(
                    json.loads(cache_file.read_text(encoding="utf-8")), symbol, start, end
                )
                if cached is not None:
                    return cached
            except Exception:
                pass

        base_params: dict[str, Any] = {
            "symbols": symbol,
            "start": f"{start.isoformat()}T00:00:00Z",
            # Alpaca's end is exclusive; this includes the report date but no later date.
            "end": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
            "sort": "desc",
            "limit": 50,
        }
        rows: list[dict[str, Any]] = []
        page_token = ""
        seen_tokens: set[str] = set()
        page_count = 0

        while True:
            if page_token:
                if page_token in seen_tokens:
                    raise RuntimeError(f"Alpaca repeated a news page token for {symbol}")
                seen_tokens.add(page_token)
            page_count += 1
            if page_count > self.max_pages:
                raise RuntimeError(
                    f"Alpaca news for {symbol} exceeded the {self.max_pages}-page safety limit"
                )
            params = dict(base_params)
            if page_token:
                params["page_token"] = page_token
            self.gate.wait()
            response = self._client().get(ALPACA_NEWS_URL, params=params, timeout=60)
            if response.status_code in {401, 403}:
                raise RuntimeError(
                    "Alpaca news access was rejected; check the API credentials and market-data plan"
                )
            response.raise_for_status()
            payload = response.json()
            page_rows = payload.get("news") or []
            if not isinstance(page_rows, list):
                page_rows = []
            rows.extend(row for row in page_rows if isinstance(row, dict))
            page_token = str(payload.get("next_page_token") or "").strip()
            if not page_token:
                break

        cache_payload = {
            "version": NEWS_CACHE_VERSION,
            "symbol": symbol,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "complete": True,
            "pages": page_count,
            "rows": rows,
        }
        temporary = cache_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(cache_payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(cache_file)
        if page_count > 1:
            print(f"Alpaca news {symbol}: downloaded {len(rows)} records across {page_count} pages")
        return rows

    def news_for_symbols(
        self, symbols: Iterable[str], start: date, end: date
    ) -> dict[str, list[dict[str, Any]]]:
        wanted = list(dict.fromkeys(str(symbol).upper() for symbol in symbols if symbol))
        result: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in wanted}
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._fetch_one, symbol, start, end): symbol for symbol in wanted}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    result[symbol] = future.result()
                except Exception as exc:
                    message = f"Alpaca news unavailable for {symbol}: {exc}"
                    print(f"warning: {message}")
                    errors.append(message)
        if errors and self.strict:
            raise RuntimeError("; ".join(errors[:5]))
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


def _symbols(item: dict[str, Any]) -> list[str]:
    values = item.get("symbols") or []
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    if not isinstance(values, list):
        return []
    return [str(value).upper().strip() for value in values if str(value).strip()]


def prepare_news_catalog(
    rows: Iterable[dict[str, Any]], symbol: str, report_date: date
) -> list[dict[str, Any]]:
    """Keep every unique in-window article associated with the requested symbol."""
    start, end = news_window(report_date)
    target = symbol.upper().strip()
    result: list[tuple[datetime, dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        published = _published_datetime(row)
        if published is None or not (start <= published.date() <= end):
            continue
        headline = _plain_text(row.get("headline"))
        if not headline:
            continue
        tagged_symbols = _symbols(row)
        if tagged_symbols and target not in tagged_symbols:
            continue
        url = str(row.get("url") or "").strip()
        article_id = str(row.get("id") or "").strip()
        key = url or article_id or f"{published.isoformat()}|{headline.lower()}"
        if key in seen:
            continue
        seen.add(key)
        result.append((published, row))
    result.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _, row in result]


def _summary_paragraphs(item: dict[str, Any], *, max_paragraphs: int = 3) -> list[str]:
    text = _plain_text(item.get("summary"))
    if not text:
        text = _plain_text(item.get("content"))[:1600]
    if not text:
        return []
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return paragraphs[:max_paragraphs] if paragraphs else [text]
