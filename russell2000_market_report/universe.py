from __future__ import annotations

import csv
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

DEFAULT_HOLDINGS_URL = (
    "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
    "latest-holdings.csv"
)


@dataclass(frozen=True)
class UniverseSnapshot:
    symbols: frozenset[str]
    source: str
    as_of: str
    fetched_at: str
    method: str

    def to_json(self) -> dict[str, object]:
        return {
            "symbols": sorted(self.symbols),
            "source": self.source,
            "as_of": self.as_of,
            "fetched_at": self.fetched_at,
            "method": self.method,
        }


def normalize_symbol(value: object) -> str | None:
    symbol = str(value or "").strip().upper()
    if not symbol or symbol in {"-", "--", "N/A", "CASH", "USD"}:
        return None
    symbol = symbol.replace("/", ".").replace("-", ".").replace(" ", "")
    symbol = re.sub(r"\.+", ".", symbol).strip(".")
    if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol):
        return None
    return symbol


def parse_ishares_holdings_csv(text: str) -> tuple[frozenset[str], str]:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    header_index = None
    for index, row in enumerate(rows):
        normalized = [cell.strip().lower() for cell in row]
        if normalized and normalized[0] == "ticker" and "asset class" in normalized:
            header_index = index
            break
    if header_index is None:
        raise ValueError("iShares holdings response does not contain the expected CSV header")

    header = [cell.strip() for cell in rows[header_index]]
    ticker_index = header.index("Ticker")
    asset_index = header.index("Asset Class")
    symbols: set[str] = set()
    for row in rows[header_index + 1 :]:
        if len(row) <= max(ticker_index, asset_index):
            continue
        asset_class = row[asset_index].strip().lower()
        if asset_class and "equity" not in asset_class:
            continue
        symbol = normalize_symbol(row[ticker_index])
        if symbol:
            symbols.add(symbol)

    as_of = ""
    for row in rows[:header_index]:
        if row and "holdings as of" in row[0].lower() and len(row) > 1:
            as_of = row[1].strip()
            break
    return frozenset(symbols), as_of


def validate_universe(symbols: Iterable[str], *, minimum: int, maximum: int) -> frozenset[str]:
    normalized = frozenset(
        symbol
        for value in symbols
        if (symbol := normalize_symbol(value)) is not None
    )
    if not minimum <= len(normalized) <= maximum:
        raise ValueError(
            f"Russell 2000 universe size {len(normalized)} is outside the expected "
            f"range {minimum}-{maximum}"
        )
    return normalized


def filter_grouped_frame(frame: pd.DataFrame, symbols: Iterable[str]) -> pd.DataFrame:
    if "symbol" not in frame.columns:
        raise ValueError("Grouped-daily frame is missing symbol")
    universe = frozenset(symbols)
    normalized = frame["symbol"].map(normalize_symbol)
    return frame[normalized.isin(universe)].copy()


def _load_cache(path: Path, *, minimum: int, maximum: int) -> UniverseSnapshot | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        symbols = validate_universe(payload.get("symbols") or [], minimum=minimum, maximum=maximum)
        fetched_at = str(payload.get("fetched_at") or "")
        return UniverseSnapshot(
            symbols=symbols,
            source=str(payload.get("source") or "cached iShares IWM holdings"),
            as_of=str(payload.get("as_of") or ""),
            fetched_at=fetched_at,
            method=str(payload.get("method") or "iShares IWM holdings proxy"),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _load_override(path: Path, *, minimum: int, maximum: int) -> UniverseSnapshot:
    text = path.read_text(encoding="utf-8-sig")
    if "Asset Class" in text and "Ticker" in text:
        symbols, as_of = parse_ishares_holdings_csv(text)
    else:
        rows = csv.reader(io.StringIO(text))
        symbols = frozenset(
            symbol
            for row in rows
            if row and (symbol := normalize_symbol(row[0])) is not None and symbol != "TICKER"
        )
        as_of = ""
    symbols = validate_universe(symbols, minimum=minimum, maximum=maximum)
    return UniverseSnapshot(
        symbols=symbols,
        source=f"repository override: {path.name}",
        as_of=as_of,
        fetched_at=datetime.now(UTC).isoformat(),
        method="user-supplied Russell 2000 constituent file",
    )


def load_russell2000_universe(cache_dir: Path, *, force_refresh: bool = False) -> UniverseSnapshot:
    minimum = int(os.getenv("RUSSELL2000_MIN_CONSTITUENTS", "1500"))
    maximum = int(os.getenv("RUSSELL2000_MAX_CONSTITUENTS", "2500"))
    override = os.getenv("RUSSELL2000_CONSTITUENTS_FILE", "").strip()
    if override:
        return _load_override(Path(override).expanduser(), minimum=minimum, maximum=maximum)

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "russell2000-universe.json"
    cached = _load_cache(cache_file, minimum=minimum, maximum=maximum)
    now = datetime.now(UTC)
    if cached and not force_refresh:
        try:
            fetched_at = datetime.fromisoformat(cached.fetched_at.replace("Z", "+00:00"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            if now - fetched_at <= timedelta(hours=18):
                return cached
        except ValueError:
            pass

    url = os.getenv("RUSSELL2000_HOLDINGS_URL", "").strip() or DEFAULT_HOLDINGS_URL
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Russell2000MarketReport/1.0 (+https://github.com/zencolab/usstock)",
                "Accept": "text/csv,text/plain,*/*",
                "Referer": "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf",
            },
            timeout=60,
        )
        response.raise_for_status()
        response_text = response.text
        content_type = response.headers.get("Content-Type", "").lower()
        leading_text = response_text.lstrip("\ufeff\n\r\t ")[:100].lower()
        if "text/html" in content_type or leading_text.startswith(("<!doctype html", "<html")):
            raise ValueError(
                "iShares holdings endpoint returned HTML instead of the holdings CSV"
            )
        symbols, as_of = parse_ishares_holdings_csv(response_text)
        symbols = validate_universe(symbols, minimum=minimum, maximum=maximum)
        snapshot = UniverseSnapshot(
            symbols=symbols,
            source="iShares IWM holdings CSV",
            as_of=as_of,
            fetched_at=now.isoformat(),
            method="IWM holdings used as a practical Russell 2000 tracking proxy",
        )
        cache_file.write_text(
            json.dumps(snapshot.to_json(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return snapshot
    except Exception as exc:
        if cached:
            try:
                fetched_at = datetime.fromisoformat(cached.fetched_at.replace("Z", "+00:00"))
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=UTC)
                if now - fetched_at <= timedelta(days=14):
                    print(f"warning: using cached Russell 2000 universe after download failure: {exc}")
                    return cached
            except ValueError:
                pass
        raise RuntimeError(f"Unable to load a validated Russell 2000 universe: {exc}") from exc
