from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

from news_translation import AlpacaNewsClient, CachedNeuralTranslator, bilingual_news


def install(namespace: dict[str, Any]) -> None:
    """Add bilingual industries and ranked historical news to the live report."""

    cache_dir = namespace["CACHE_DIR"]
    original_live_payload = namespace["live_payload"]
    original_write_outputs = namespace["write_outputs"]
    original_public_stock_record = namespace["public_stock_record"]

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
        translator = CachedNeuralTranslator(cache_dir)

        print("Downloading up to three months of Alpaca/Benzinga news …")
        news_by_symbol = client.news_for_symbols(
            stocks,
            config.report_date - timedelta(days=92),
            config.report_date,
        )
        for position, (symbol, stock) in enumerate(stocks.items(), start=1):
            print(f"[{position}/{len(stocks)}] translating industry and important news for {symbol}")
            industry = str(stock.get("industry") or "")
            stock["industry_zh"] = translator.translate(industry, industry=True) if industry else ""
            stock["news"] = bilingual_news(
                news_by_symbol.get(symbol, []),
                config.report_date,
                translator,
                limit=5,
            )
        translator.flush()
        return payload

    def write_outputs(config: Any, payload: dict[str, Any]) -> None:
        original_write_outputs(config, payload)
        if config.mode != "live":
            return
        metadata_file = config.output / "metadata.json"
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        sources = list(metadata.get("sources") or [])
        for source in [
            "Alpaca/Benzinga historical news",
            "Argos local neural translation (English to Chinese)",
        ]:
            if source not in sources:
                sources.append(source)
        metadata["sources"] = sources
        metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    def public_stock_record(stock: dict[str, Any]) -> dict[str, Any]:
        record = original_public_stock_record(stock)
        record["industry_zh"] = stock.get("industry_zh")
        record["news"] = stock.get("news") or []
        return record

    namespace["live_payload"] = live_payload
    namespace["write_outputs"] = write_outputs
    namespace["public_stock_record"] = public_stock_record
