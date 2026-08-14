from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ALPACA_DATA_BASE = "https://data.alpaca.markets/v2"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_DATA_BASE = "https://data.sec.gov"


def _retry_session(headers: dict[str, str]) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(headers)
    return session


class AlpacaClient:
    """Batch daily-bar client used only for selected mover histories."""

    def __init__(self, key_id: str, secret_key: str, *, feed: str = "sip") -> None:
        if not key_id or not secret_key:
            raise ValueError("Alpaca API credentials are required")
        self.key_id = key_id
        self.secret_key = secret_key
        self.feed = feed.strip().lower() or "sip"

    def _session(self) -> requests.Session:
        return _retry_session(
            {
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Accept": "application/json",
                "User-Agent": "USMarketCloseReport/2.0",
            }
        )

    def histories(
        self,
        symbols: Iterable[str],
        start: date,
        end: date,
        *,
        chunk_size: int = 75,
    ) -> dict[str, pd.DataFrame]:
        wanted = list(dict.fromkeys(str(symbol).upper() for symbol in symbols if symbol))
        rows_by_symbol: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in wanted}
        session = self._session()
        inclusive_end = end + timedelta(days=1)

        for offset in range(0, len(wanted), max(1, chunk_size)):
            chunk = wanted[offset : offset + chunk_size]
            page_token: str | None = None
            while True:
                params: dict[str, Any] = {
                    "symbols": ",".join(chunk),
                    "timeframe": "1Day",
                    "start": start.isoformat(),
                    "end": inclusive_end.isoformat(),
                    "adjustment": "all",
                    "feed": self.feed,
                    "sort": "asc",
                    "limit": 10000,
                }
                if page_token:
                    params["page_token"] = page_token
                response = session.get(f"{ALPACA_DATA_BASE}/stocks/bars", params=params, timeout=90)
                if response.status_code in {401, 403}:
                    raise RuntimeError(
                        f"Alpaca rejected feed={self.feed!r}. Check API credentials and data entitlement; "
                        "set repository variable ALPACA_FEED=iex only if IEX coverage is acceptable."
                    )
                response.raise_for_status()
                payload = response.json()
                bars = payload.get("bars") or {}
                if not isinstance(bars, dict):
                    raise ValueError("Unexpected Alpaca bars response")
                for symbol, symbol_rows in bars.items():
                    normalized = str(symbol).upper()
                    if normalized in rows_by_symbol and isinstance(symbol_rows, list):
                        rows_by_symbol[normalized].extend(symbol_rows)
                page_token = payload.get("next_page_token")
                if not page_token:
                    break

        result: dict[str, pd.DataFrame] = {}
        for symbol, rows in rows_by_symbol.items():
            frame = pd.DataFrame(rows)
            if frame.empty or "t" not in frame.columns:
                result[symbol] = pd.DataFrame(columns=["date", "close", "volume"])
                continue
            frame["date"] = pd.to_datetime(frame["t"], utc=True, errors="coerce").dt.date
            frame["close"] = pd.to_numeric(frame.get("c"), errors="coerce")
            frame["volume"] = pd.to_numeric(frame.get("v"), errors="coerce")
            result[symbol] = (
                frame[["date", "close", "volume"]]
                .dropna(subset=["date", "close"])
                .drop_duplicates("date", keep="last")
                .sort_values("date")
                .reset_index(drop=True)
            )
        return result


class _RequestGate:
    def __init__(self, requests_per_second: float) -> None:
        self.interval = 1.0 / max(requests_per_second, 0.1)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            if delay:
                time.sleep(delay)
            self.next_at = max(now, self.next_at) + self.interval


class SecBulkClient:
    """Rate-limited concurrent SEC downloader plus the official ticker/CIK map."""

    def __init__(
        self,
        user_agent: str,
        cache_dir: Path,
        *,
        requests_per_second: float = 5.0,
        workers: int = 6,
    ) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC user agent must include a contact email")
        self.user_agent = user_agent
        self.cache_dir = cache_dir / "sec"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.gate = _RequestGate(requests_per_second)
        self.workers = max(1, min(workers, 8))
        self.local = threading.local()

    def _session(self) -> requests.Session:
        session = getattr(self.local, "session", None)
        if session is None:
            session = _retry_session(
                {
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                }
            )
            self.local.session = session
        return session

    def _get_json(self, url: str) -> dict[str, Any]:
        self.gate.wait()
        response = self._session().get(url, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Expected a JSON object from {url}")
        return payload

    @staticmethod
    def _normalize_cik(value: str | int | None) -> str | None:
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits.zfill(10) if digits else None

    def ticker_map(self, *, max_age_days: int = 7) -> dict[str, dict[str, Any]]:
        cache_file = self.cache_dir / "company_tickers_exchange.json"
        payload: dict[str, Any] | None = None
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < max_age_days * 86400:
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                payload = cached if isinstance(cached, dict) else None
            except Exception:
                payload = None
        if payload is None:
            payload = self._get_json(SEC_TICKERS_URL)
            cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        fields = payload.get("fields") or []
        data = payload.get("data") or []
        result: dict[str, dict[str, Any]] = {}
        for values in data:
            if not isinstance(values, list):
                continue
            row = dict(zip(fields, values))
            symbol = str(row.get("ticker") or "").upper().strip()
            if not symbol:
                continue
            item = {
                "cik": self._normalize_cik(row.get("cik")),
                "name": str(row.get("name") or symbol),
                "exchange": str(row.get("exchange") or ""),
            }
            for alias in {symbol, symbol.replace("-", "."), symbol.replace(".", "-")}:
                result.setdefault(alias, item)
        return result

    def company_payloads(self, ciks: Iterable[str | int | None]) -> dict[str, dict[str, Any]]:
        normalized = [cik for cik in dict.fromkeys(self._normalize_cik(value) for value in ciks) if cik]
        results: dict[str, dict[str, Any]] = {}

        def load_one(cik: str) -> tuple[str, dict[str, Any]]:
            facts: dict[str, Any] = {}
            submissions: dict[str, Any] = {}
            try:
                facts = self._get_json(f"{SEC_DATA_BASE}/api/xbrl/companyfacts/CIK{cik}.json")
            except requests.RequestException as exc:
                print(f"warning: SEC company facts unavailable for CIK {cik}: {exc}")
            try:
                submissions = self._get_json(f"{SEC_DATA_BASE}/submissions/CIK{cik}.json")
            except requests.RequestException as exc:
                print(f"warning: SEC submissions unavailable for CIK {cik}: {exc}")
            return cik, {"facts": facts, "submissions": submissions}

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(load_one, cik): cik for cik in normalized}
            for future in as_completed(futures):
                cik = futures[future]
                try:
                    key, value = future.result()
                    results[key] = value
                except Exception as exc:
                    print(f"warning: SEC enrichment failed for CIK {cik}: {exc}")
                    results[cik] = {"facts": {}, "submissions": {}}
        return results
