from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

from news_translation import AlpacaNewsClient
from premium_translation import CachedAiTranslator, bilingual_news, collect_news_sources


def install(namespace: dict[str, Any]) -> None:
    """Add bilingual industries and ranked historical news to the live report."""

    cache_dir = namespace["CACHE_DIR"]
    original_live_payload = namespace["live_payload"]
    original_write_outputs = namespace["write_outputs"]
    original_public_stock_record = namespace["public_stock_record"]
    translation_source = {"label": "AI translation (English to Chinese)"}

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
        secret_key = (os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY") or "").strip()
        client = AlpacaNewsClient(key_id, secret_key, cache_dir)
        translator = CachedAiTranslator(cache_dir)

        print("Downloading up to three months of Alpaca/Benzinga news …")
        news_by_symbol = client.news_for_symbols(
            stocks,
            config.report_date - timedelta(days=92),
            config.report_date,
        )
        industries = list(dict.fromkeys(
            str(stock.get("industry") or "") for stock in stocks.values() if stock.get("industry")
        ))
        industry_targets = translator.translate_many(industries, industry=True)
        industry_map = dict(zip(industries, industry_targets))
        news_sources = collect_news_sources(news_by_symbol, config.report_date, limit=5)
        print(f"Batch-translating {len(news_sources)} unique news texts with {translator.source_label} …")
        translator.translate_many(news_sources)
        for position, (symbol, stock) in enumerate(stocks.items(), start=1):
            print(f"[{position}/{len(stocks)}] translating industry and important news for {symbol}")
            industry = str(stock.get("industry") or "")
            stock["industry_zh"] = industry_map.get(industry, "")
            stock["news"] = bilingual_news(
                news_by_symbol.get(symbol, []),
                config.report_date,
                translator,
                limit=5,
            )
        translator.flush()
        translation_source["label"] = translator.source_label
        return payload

    def write_outputs(config: Any, payload: dict[str, Any]) -> None:
        original_write_outputs(config, payload)
        if config.mode != "live":
            return
        metadata_file = config.output / "metadata.json"
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        sources = [
            source
            for source in list(metadata.get("sources") or [])
            if "translation" not in str(source).lower() and "argos" not in str(source).lower()
        ]
        for source in ["Alpaca/Benzinga historical news", translation_source["label"]]:
            if source not in sources:
                sources.append(source)
        metadata["sources"] = sources
        metadata["translation_provider"] = translation_source["label"]
        metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def public_stock_record(stock: dict[str, Any]) -> dict[str, Any]:
        record = original_public_stock_record(stock)
        record["industry_zh"] = stock.get("industry_zh")
        record["news"] = stock.get("news") or []
        return record

    namespace["live_payload"] = live_payload
    namespace["write_outputs"] = write_outputs
    namespace["public_stock_record"] = public_stock_record
