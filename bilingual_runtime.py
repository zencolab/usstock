from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from news_translation import (
    AlpacaNewsClient,
    news_window,
    prepare_news_catalog,
)
from premium_translation import CachedAiTranslator, bilingual_news, collect_news_sources


def install(namespace: dict[str, Any]) -> None:
    """Add bilingual industries and a complete three-month news catalog."""

    cache_dir = namespace["CACHE_DIR"]
    original_live_payload = namespace["live_payload"]
    original_write_outputs = namespace["write_outputs"]
    original_public_stock_record = namespace["public_stock_record"]
    template_dir = Path(__file__).resolve().parent / "templates"
    runtime_state: dict[str, Any] = {
        "translation_label": "Ollama AI translation (English to Chinese)",
        "window_start": "",
        "window_end": "",
        "news_count": 0,
    }

    def live_payload(config: Any) -> dict[str, Any]:
        payload = original_live_payload(config)
        stocks: dict[str, dict[str, Any]] = {}
        for stock in [*(payload.get("gainers") or []), *(payload.get("losers") or [])]:
            symbol = str(stock.get("symbol") or "").upper()
            if symbol:
                stocks[symbol] = stock
        if not stocks:
            return payload

        key_id = (os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID") or "").strip()
        secret_key = (
            os.getenv("ALPACA_API_SECRET_KEY")
            or os.getenv("APCA_API_SECRET_KEY")
            or ""
        ).strip()
        start, end = news_window(config.report_date)
        client = AlpacaNewsClient(
            key_id,
            secret_key,
            cache_dir,
            requests_per_minute=float(os.getenv("ALPACA_NEWS_RPM", "180")),
            max_pages=int(os.getenv("ALPACA_NEWS_MAX_PAGES", "100")),
            strict=True,
        )
        translator = CachedAiTranslator(cache_dir)

        print(
            f"Downloading complete Alpaca/Benzinga news catalogs for {start.isoformat()} "
            f"through {end.isoformat()} …"
        )
        raw_news = client.news_for_symbols(stocks, start, end)
        news_by_symbol = {
            symbol: prepare_news_catalog(raw_news.get(symbol, []), symbol, config.report_date)
            for symbol in stocks
        }
        industries = list(
            dict.fromkeys(
                str(stock.get("industry") or "")
                for stock in stocks.values()
                if stock.get("industry")
            )
        )
        industry_targets = translator.translate_many(industries, industry=True)
        industry_map = dict(zip(industries, industry_targets))

        news_sources = collect_news_sources(news_by_symbol)
        print(
            f"Batch-translating all {len(news_sources)} unique news titles/summary paragraphs "
            f"with {translator.source_label} …"
        )
        translator.translate_many(news_sources)
        total_news = 0
        for position, (symbol, stock) in enumerate(stocks.items(), start=1):
            catalog = news_by_symbol.get(symbol, [])
            print(
                f"[{position}/{len(stocks)}] rendering {len(catalog)} news items for {symbol}"
            )
            industry = str(stock.get("industry") or "")
            stock["industry_zh"] = industry_map.get(industry, "")
            stock["news"] = bilingual_news(catalog, translator, symbol=symbol)
            stock["news_window_start"] = start.isoformat()
            stock["news_window_end"] = end.isoformat()
            stock["news_catalog_mode"] = "all_titles"
            stock["translation_provider"] = translator.source_label
            total_news += len(stock["news"])

        translator.flush()
        runtime_state.update(
            {
                "translation_label": translator.source_label,
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
                "news_count": total_news,
            }
        )
        return payload

    def write_outputs(config: Any, payload: dict[str, Any]) -> None:
        original_write_outputs(config, payload)
        if config.mode != "live":
            return

        news_dir = config.output / "news"
        news_dir.mkdir(parents=True, exist_ok=True)
        env = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        detail_template = env.get_template("news.html.j2")
        seen_pages: set[str] = set()
        detail_count = 0
        for stock in [*(payload.get("gainers") or []), *(payload.get("losers") or [])]:
            for article in stock.get("news") or []:
                detail_file = str(article.get("detail_file") or "")
                if not detail_file or detail_file in seen_pages:
                    continue
                seen_pages.add(detail_file)
                rendered = detail_template.render(
                    report_date=config.report_date.isoformat(),
                    stock=stock,
                    article=article,
                )
                (news_dir / detail_file).write_text(rendered, encoding="utf-8")
                detail_count += 1

        metadata_file = config.output / "metadata.json"
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        sources = [
            source
            for source in list(metadata.get("sources") or [])
            if "translation" not in str(source).lower()
            and "argos" not in str(source).lower()
            and "historical news" not in str(source).lower()
        ]
        for source in [
            "Alpaca/Benzinga complete three-calendar-month news catalog",
            runtime_state["translation_label"],
        ]:
            if source not in sources:
                sources.append(source)
        metadata.update(
            {
                "sources": sources,
                "translation_provider": runtime_state["translation_label"],
                "news_catalog_mode": "all_titles",
                "news_window_start": runtime_state["window_start"],
                "news_window_end": runtime_state["window_end"],
                "news_items": runtime_state["news_count"],
                "news_detail_pages": detail_count,
            }
        )
        metadata_file.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def public_stock_record(stock: dict[str, Any]) -> dict[str, Any]:
        record = original_public_stock_record(stock)
        record["industry_zh"] = stock.get("industry_zh")
        record["news"] = stock.get("news") or []
        record["news_window_start"] = stock.get("news_window_start")
        record["news_window_end"] = stock.get("news_window_end")
        record["news_catalog_mode"] = stock.get("news_catalog_mode")
        record["translation_provider"] = stock.get("translation_provider")
        return record

    namespace["live_payload"] = live_payload
    namespace["write_outputs"] = write_outputs
    namespace["public_stock_record"] = public_stock_record
