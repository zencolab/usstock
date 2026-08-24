from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from russell2000_market_report.universe import (
    UniverseSnapshot,
    filter_grouped_frame,
    load_russell2000_universe,
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


def brand_output(site: Path) -> None:
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
                "成分股范围：iShares IWM 持仓（罗素 2000 跟踪代理）；来源：Massive",
            )
        path.write_text(content, encoding="utf-8")


def install(namespace: dict[str, Any], project_root: Path) -> None:
    original_live_payload = namespace["live_payload"]
    original_write_outputs = namespace["write_outputs"]
    MassiveClient = namespace["MassiveClient"]
    state: dict[str, Any] = {"snapshot": None, "matched": {}}

    def live_payload(config: Any) -> dict[str, Any]:
        snapshot = load_russell2000_universe(project_root / ".cache" / "universe")
        original_grouped_daily = MassiveClient.grouped_daily
        matched: dict[str, int] = {}

        def grouped_daily(client: Any, day: Any):
            full_market = original_grouped_daily(client, day)
            filtered = filter_grouped_frame(full_market, snapshot.symbols)
            matched[str(day)] = len(filtered)
            minimum_rows = max(config.top_n * 2, 1000)
            if len(filtered) < minimum_rows:
                raise RuntimeError(
                    f"Only {len(filtered)} Russell 2000 constituents matched Massive on {day}; "
                    f"expected at least {minimum_rows}"
                )
            print(
                f"Russell 2000 filter: {len(filtered)} of {len(full_market)} grouped-daily rows "
                f"matched on {day}"
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
        payload["russell2000_universe"] = {
            "constituents": len(snapshot.symbols),
            "source": snapshot.source,
            "as_of": snapshot.as_of,
            "matched": matched,
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
        metadata.update(
            {
                "sources": sources,
                "universe_name": "Russell 2000",
                "universe_source": universe_source,
                "universe_method": universe_method,
                "universe_as_of": universe_as_of,
                "universe_constituents": universe_count,
                "universe_matched_by_date": state.get("matched") or {},
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
        brand_output(config.output)

    namespace["live_payload"] = live_payload
    namespace["write_outputs"] = write_outputs
