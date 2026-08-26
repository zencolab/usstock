from __future__ import annotations

import json
import os
import threading
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

from hybrid_data import AlpacaClient
from russell2000_market_report.universe import (
    UniverseSnapshot,
    filter_grouped_frame,
    load_russell2000_universe,
    normalize_symbol,
)

_PATCH_LOCK = threading.Lock()


def _brand_html(content: str) -> str:
    replacements = {
        "美股收盘日报": "罗素 2000 收盘日报",
        "US Market Close": "Russell 2000 Market Close",
        "全市场涨跌幅榜": "罗素 2000 成分股涨跌幅榜",
        "全市场按收盘价": "罗素 2000 成分股按收盘价",
    }
    for old, new in replacements.items():
        content = content.replace(old, new)
    return content


def _market_source_label(market_sources: Iterable[str]) -> str:
    unique = list(dict.fromkeys(str(value) for value in market_sources if value))
    return " / ".join(unique) if unique else "Massive grouped daily"


def brand_output(site: Path, market_sources: Iterable[str] = ()) -> None:
    market_label = _market_source_label(market_sources)
    for path in site.rglob("*.html"):
        content = _brand_html(path.read_text(encoding="utf-8"))
        if path.name == "index.html" and "股票池：罗素 2000" not in content:
            content = content.replace(
                '<div class="meta-row">',
                '<div class="meta-row">\n      <span class="source-badge">股票池：罗素 2000</span>',
                1,
            )
        if "iShares IWM" not in content:
            content = content.replace(
                "来源：Massive",
                f"成分股范围：iShares IWM 持仓（罗素 2000 跟踪代理）；涨跌排行行情：{market_label}；来源：Massive",
            )
        path.write_text(content, encoding="utf-8")


def _is_massive_entitlement_error(exc: BaseException) -> bool:
    if not isinstance(exc, requests.HTTPError):
        return False
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) in {401, 403}


def alpaca_grouped_daily(
    client: Any,
    symbols: Iterable[str],
    day: date,
) -> pd.DataFrame:
    wanted = list(
        dict.fromkeys(
            symbol
            for value in symbols
            if (symbol := normalize_symbol(value)) is not None
        )
    )
    histories = client.histories(wanted, day, day, chunk_size=75)
    rows: list[dict[str, Any]] = []
    for symbol in wanted:
        frame = histories.get(symbol)
        if frame is None or frame.empty or "date" not in frame.columns:
            continue
        matching = frame[frame["date"] == day]
        if matching.empty:
            continue
        latest = matching.iloc[-1]
        close = pd.to_numeric(latest.get("close"), errors="coerce")
        volume = pd.to_numeric(latest.get("volume"), errors="coerce")
        if pd.isna(close) or pd.isna(volume):
            continue
        rows.append(
            {
                "symbol": symbol,
                "close": float(close),
                "volume": float(volume),
            }
        )
    return pd.DataFrame(rows, columns=["symbol", "close", "volume"])


def install(namespace: dict[str, Any], project_root: Path) -> None:
    original_live_payload = namespace["live_payload"]
    original_write_outputs = namespace["write_outputs"]
    MassiveClient = namespace["MassiveClient"]
    state: dict[str, Any] = {
        "snapshot": None,
        "matched": {},
        "market_sources": {},
    }

    def live_payload(config: Any) -> dict[str, Any]:
        snapshot = load_russell2000_universe(project_root / ".cache" / "universe")
        original_grouped_daily = MassiveClient.grouped_daily
        matched: dict[str, int] = {}
        market_sources: dict[str, str] = {}
        fallback_client: AlpacaClient | None = None

        def grouped_daily(client: Any, day: date) -> pd.DataFrame:
            nonlocal fallback_client
            source_label = "Massive grouped daily"
            try:
                full_market = original_grouped_daily(client, day)
                filtered = filter_grouped_frame(full_market, snapshot.symbols)
            except requests.HTTPError as exc:
                if not _is_massive_entitlement_error(exc):
                    raise
                key_id = (
                    os.getenv("ALPACA_API_KEY_ID")
                    or os.getenv("APCA_API_KEY_ID")
                    or ""
                ).strip()
                secret_key = (
                    os.getenv("ALPACA_API_SECRET_KEY")
                    or os.getenv("APCA_API_SECRET_KEY")
                    or ""
                ).strip()
                if not key_id or not secret_key:
                    raise RuntimeError(
                        "Massive grouped daily returned 401/403 and Alpaca fallback "
                        "credentials are missing"
                    ) from exc
                if fallback_client is None:
                    feed = os.getenv("ALPACA_FEED", "iex").strip().lower() or "iex"
                    fallback_client = AlpacaClient(key_id, secret_key, feed=feed)
                status_code = getattr(getattr(exc, "response", None), "status_code", "403")
                source_label = (
                    f"Alpaca {fallback_client.feed.upper()} daily bars "
                    f"(Massive HTTP {status_code} fallback)"
                )
                print(
                    f"warning: Massive grouped daily is unavailable ({status_code}); "
                    f"using {source_label} for {day}"
                )
                filtered = alpaca_grouped_daily(
                    fallback_client,
                    snapshot.symbols,
                    day,
                )
                full_market = filtered

            matched[str(day)] = len(filtered)
            market_sources[str(day)] = source_label
            minimum_rows = max(config.top_n * 2, 1000)
            if len(filtered) < minimum_rows:
                raise RuntimeError(
                    f"Only {len(filtered)} Russell 2000 constituents matched {source_label} on {day}; "
                    f"expected at least {minimum_rows}"
                )
            print(
                f"Russell 2000 filter: {len(filtered)} of {len(full_market)} rows "
                f"matched on {day} via {source_label}"
            )
            return filtered

        with _PATCH_LOCK:
            MassiveClient.grouped_daily = grouped_daily
            try:
                payload = original_live_payload(config)
            finally:
                MassiveClient.grouped_daily = original_grouped_daily

        state["snapshot"] = snapshot
        state["matched"] = matched
        state["market_sources"] = market_sources
        payload["russell2000_universe"] = {
            "constituents": len(snapshot.symbols),
            "source": snapshot.source,
            "as_of": snapshot.as_of,
            "matched": matched,
            "market_data_by_date": market_sources,
        }
        return payload

    def write_outputs(config: Any, payload: dict[str, Any]) -> None:
        original_write_outputs(config, payload)
        metadata_path = config.output / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        snapshot = state.get("snapshot")
        sources = list(metadata.get("sources") or [])
        universe_source = "demo fixtures"
        universe_count = 0
        universe_as_of = ""
        universe_method = "demo mode; live runs use validated iShares IWM holdings"
        if isinstance(snapshot, UniverseSnapshot):
            universe_source = snapshot.source
            universe_count = len(snapshot.symbols)
            universe_as_of = snapshot.as_of
            universe_method = snapshot.method
            label = "iShares IWM holdings (Russell 2000 tracking proxy)"
            if label not in sources:
                sources.append(label)
        market_sources = dict(state.get("market_sources") or {})
        fallback_labels = [
            value for value in market_sources.values() if str(value).startswith("Alpaca ")
        ]
        if fallback_labels:
            label = "Alpaca daily bars fallback for Russell 2000 mover ranking"
            if label not in sources:
                sources.append(label)
        metadata.update(
            {
                "sources": sources,
                "universe_name": "Russell 2000",
                "universe_source": universe_source,
                "universe_method": universe_method,
                "universe_as_of": universe_as_of,
                "universe_constituents": universe_count,
                "universe_matched_by_date": state.get("matched") or {},
                "ranking_market_data_by_date": market_sources,
                "selection_rule": (
                    f"Rank eligible Russell 2000 constituents by adjusted close versus prior "
                    f"adjusted close; select top {config.top_n} gainers and top {config.top_n} losers"
                ),
            }
        )
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        brand_output(config.output, market_sources.values())

    namespace["live_payload"] = live_payload
    namespace["write_outputs"] = write_outputs
