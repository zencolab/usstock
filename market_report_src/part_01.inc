from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import os
import random
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
try:
    import pandas_market_calendars as mcal
except ImportError:  # Local demo fallback; GitHub installs the full NYSE calendar.
    mcal = None
import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
TEMPLATES = ROOT / "templates"
CONCEPTS_FILE = ROOT / "config" / "concepts.json"
CACHE_DIR = ROOT / ".cache"
MASSIVE_BASE = "https://api.polygon.io"
SEC_BASE = "https://data.sec.gov"
FINRA_DAILY = "https://" + "cdn.finra.org/equity/regsho/daily/CNMSshvol{day}.txt"
SEC_ARCHIVES = "https://" + "www.sec.gov/Archives/edgar/data"

INDEX_SERIES = [
    ("NASDAQCOM", "纳斯达克综合指数"),
    ("DJIA", "道琼斯工业指数"),
    ("SP500", "标普 500"),
]


@dataclass
class RunConfig:
    mode: str
    report_date: date
    previous_date: date
    top_n: int
    months: int
    min_price: float
    min_dollar_volume: float
    output: Path
    data_output: Path


class RateLimiter:
    def __init__(self, per_minute: float) -> None:
        self.interval = 60.0 / max(per_minute, 0.1)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            delay = max(0.0, self.next_at - now)
            if delay:
                time.sleep(delay)
            self.next_at = max(now, self.next_at) + self.interval


class HttpClient:
    def __init__(self, user_agent: str) -> None:
        self.session = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json,text/plain,*/*"})

    def get_json(self, url: str, *, params: dict[str, Any] | None = None, timeout: int = 45) -> dict[str, Any]:
        response = self.session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object from {url}")
        return value

    def get_text(self, url: str, *, params: dict[str, Any] | None = None, timeout: int = 45) -> str:
        response = self.session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.text


class MassiveClient:
    def __init__(self, api_key: str, rpm: float, http: HttpClient) -> None:
        self.api_key = api_key
        self.rate = RateLimiter(rpm)
        self.http = http

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.rate.wait()
        query = dict(params or {})
        query["apiKey"] = self.api_key
        return self.http.get_json(f"{MASSIVE_BASE}{path}", params=query)

    def grouped_daily(self, day: date) -> pd.DataFrame:
        payload = self._get(
            f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
            {"adjusted": "true"},
        )
        rows = payload.get("results") or []
        frame = pd.DataFrame(rows)
        if frame.empty:
            raise RuntimeError(f"Massive returned no grouped daily data for {day}")
        frame = frame.rename(
            columns={"T": "symbol", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vw": "vwap"}
        )
        keep = [c for c in ["symbol", "open", "high", "low", "close", "volume", "vwap"] if c in frame.columns]
        return frame[keep].copy()

    def history(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        payload = self._get(
            f"/v2/aggs/ticker/{symbol}/range/1/day/{start.isoformat()}/{end.isoformat()}",
            {"adjusted": "true", "sort": "asc", "limit": 50000},
        )
        frame = pd.DataFrame(payload.get("results") or [])
        if frame.empty:
            return pd.DataFrame(columns=["date", "close", "volume"])
        frame["date"] = pd.to_datetime(frame["t"], unit="ms", utc=True).dt.date
        frame = frame.rename(columns={"c": "close", "v": "volume"})
        return frame[["date", "close", "volume"]].copy()

    def ticker_details(self, symbol: str) -> dict[str, Any]:
        payload = self._get(f"/v3/reference/tickers/{symbol}")
        result = payload.get("results") or {}
        return result if isinstance(result, dict) else {}

    def short_interest(self, symbol: str) -> pd.DataFrame:
        try:
            payload = self._get(
                "/stocks/v1/short-interest",
                {"ticker": symbol, "limit": 100, "sort": "settlement_date.asc"},
            )
        except requests.HTTPError as exc:
            print(f"warning: short interest unavailable for {symbol}: {exc}")
