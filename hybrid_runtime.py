from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Iterable

import numpy as np
import pandas as pd

from hybrid_data import AlpacaClient, SecBulkClient


def _short_interest_batch(massive: Any, symbols: Iterable[str], start: date, end: date) -> pd.DataFrame:
    wanted = list(dict.fromkeys(str(symbol).upper() for symbol in symbols if symbol))
    columns = ["date", "symbol", "short_interest", "days_to_cover"]
    if not wanted:
        return pd.DataFrame(columns=columns)
    payload = massive._get(
        "/stocks/v1/short-interest",
        {
            "ticker.any_of": ",".join(wanted),
            "settlement_date.gte": start.isoformat(),
            "settlement_date.lte": end.isoformat(),
            "limit": 50000,
            "sort": "settlement_date.asc",
        },
    )
    frame = pd.DataFrame(payload.get("results") or [])
    if frame.empty:
        return pd.DataFrame(columns=columns)
    symbol_col = next((c for c in ["ticker", "symbol"] if c in frame.columns), None)
    date_col = next((c for c in ["settlement_date", "settlementDate", "date"] if c in frame.columns), None)
    value_col = next((c for c in ["short_interest", "shortInterest", "current_short_position"] if c in frame.columns), None)
    days_col = next((c for c in ["days_to_cover", "daysToCover"] if c in frame.columns), None)
    if not symbol_col or not date_col or not value_col:
        return pd.DataFrame(columns=columns)
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(frame[date_col], errors="coerce").dt.date,
            "symbol": frame[symbol_col].astype(str).str.upper(),
            "short_interest": pd.to_numeric(frame[value_col], errors="coerce"),
        }
    )
    out["days_to_cover"] = pd.to_numeric(frame[days_col], errors="coerce") if days_col else np.nan
    return out.dropna(subset=["date", "short_interest"]).sort_values(["symbol", "date"]).reset_index(drop=True)


def install(namespace: dict[str, Any]) -> None:
    """Replace only the live data pipeline after the legacy report source is loaded."""

    HttpClient = namespace["HttpClient"]
    MassiveClient = namespace["MassiveClient"]
    SecClient = namespace["SecClient"]
    FinraClient = namespace["FinraClient"]
    CACHE_DIR = namespace["CACHE_DIR"]
    INDEX_SERIES = namespace["INDEX_SERIES"]
    months_ago = namespace["months_ago"]
    fred_history = namespace["fred_history"]
    clean_float = namespace["clean_float"]
    load_concepts = namespace["load_concepts"]
    classify_concepts = namespace["classify_concepts"]
    safe_file_name = namespace["safe_file_name"]
    original_write_outputs = namespace["write_outputs"]

    def live_payload(config: Any) -> dict[str, Any]:
        massive_key = os.getenv("MASSIVE_API_KEY", "").strip()
        alpaca_key_id = (os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID") or "").strip()
        alpaca_secret = (os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY") or "").strip()
        sec_user_agent = os.getenv("SEC_USER_AGENT", "").strip()
        missing = [name for name, value in [("MASSIVE_API_KEY", massive_key), ("ALPACA_API_KEY_ID", alpaca_key_id), ("ALPACA_API_SECRET_KEY", alpaca_secret), ("SEC_USER_AGENT", sec_user_agent)] if not value]
        if missing:
            raise RuntimeError(f"Missing live-mode configuration: {', '.join(missing)}")
        if "@" not in sec_user_agent:
            raise RuntimeError("SEC_USER_AGENT must identify the application and include a contact email")

        massive_rpm = float(os.getenv("MASSIVE_RPM", "5"))
        alpaca_feed = os.getenv("ALPACA_FEED", "sip").strip().lower() or "sip"
        sec_rps = float(os.getenv("SEC_RPS", "5"))
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        http = HttpClient(sec_user_agent)
        massive = MassiveClient(massive_key, massive_rpm, http)
        alpaca = AlpacaClient(alpaca_key_id, alpaca_secret, feed=alpaca_feed)
        sec_parser = SecClient(http)
        sec_bulk = SecBulkClient(sec_user_agent, CACHE_DIR, requests_per_second=sec_rps)
        finra_client = FinraClient(http, CACHE_DIR)
        rules = load_concepts()
        history_start = months_ago(config.report_date, config.months)

        indices = []
        for series_id, name in INDEX_SERIES:
            frame = fred_history(http, series_id, history_start, config.report_date)
            if frame.empty:
                indices.append({"series_id": series_id, "name": name, "close": None, "change_pct": None, "history": frame})
                continue
            latest = frame.iloc[-1]
            prior = frame.iloc[-2] if len(frame) > 1 else latest
            change_pct = (latest["close"] / prior["close"] - 1) * 100 if prior["close"] else None
            indices.append({"series_id": series_id, "name": name, "close": float(latest["close"]), "change_pct": clean_float(change_pct), "history": frame})

        print("Downloading two Massive grouped-daily snapshots …")
        today = massive.grouped_daily(config.report_date)
        previous = massive.grouped_daily(config.previous_date)[["symbol", "close"]].rename(columns={"close": "previous_close"})
        movers = today.merge(previous, on="symbol", how="inner")
        for col in ["close", "previous_close", "volume"]:
            movers[col] = pd.to_numeric(movers[col], errors="coerce")
        movers["change_pct"] = (movers["close"] / movers["previous_close"] - 1) * 100
        movers["dollar_volume"] = movers["close"] * movers["volume"]
        symbol_ok = movers["symbol"].astype(str).str.match(r"^[A-Z][A-Z0-9.]{0,7}$")
        movers = movers[symbol_ok & (movers["close"] >= config.min_price) & (movers["previous_close"] > 0) & (movers["dollar_volume"] >= config.min_dollar_volume) & movers["change_pct"].replace([np.inf, -np.inf], np.nan).notna()].copy()
        gainers_base = movers.nlargest(config.top_n, "change_pct")
        losers_base = movers.nsmallest(config.top_n, "change_pct")
        if len(gainers_base) < config.top_n or len(losers_base) < config.top_n:
            raise RuntimeError("Massive grouped data did not contain enough liquid gainers and losers")
        selected = pd.concat([gainers_base, losers_base], ignore_index=True).drop_duplicates("symbol")
        selected_symbols = selected["symbol"].astype(str).tolist()

        print(f"Downloading {len(selected_symbols)} six-month histories from Alpaca in batches (feed={alpaca_feed}) …")
        histories = alpaca.histories(selected_symbols, history_start, config.report_date)
        print("Loading the SEC ticker map and company filings with a fair-access rate limit …")
        ticker_map = sec_bulk.ticker_map()
        identities = {symbol: ticker_map.get(symbol, {}) for symbol in selected_symbols}
        sec_payloads = sec_bulk.company_payloads(identity.get("cik") for identity in identities.values())

        print("Downloading short interest from Massive in one batched request …")
        try:
            short_interest_all = _short_interest_batch(massive, selected_symbols, history_start, config.report_date)
        except Exception as exc:
            print(f"warning: batched short interest unavailable: {exc}")
            short_interest_all = pd.DataFrame(columns=["date", "symbol", "short_interest", "days_to_cover"])

        enriched: dict[str, dict[str, Any]] = {}
        for position, row in selected.reset_index(drop=True).iterrows():
            symbol = str(row["symbol"])
            print(f"[{position + 1}/{len(selected)}] assembling {symbol}")
            identity = identities.get(symbol) or {}
            cik = identity.get("cik")
            sec_bundle = sec_payloads.get(str(cik), {}) if cik else {}
            facts_payload = sec_bundle.get("facts") or {}
            submissions = sec_bundle.get("submissions") or {}
            fundamentals = sec_parser.fundamentals(facts_payload) if facts_payload else {"revenue": None, "eps": None, "shares_outstanding": None}
            ownership = sec_parser.ownership_summary(submissions, cik) if submissions else {"summary": "SEC 最近申报数据不可用。", "filings": []}
            shares = fundamentals.get("shares_outstanding")
            close = clean_float(row["close"])
            market_cap = close * shares if close is not None and shares else None
            eps = fundamentals.get("eps")
            pe = close / eps if close is not None and eps is not None and eps > 0 else None
            volume = clean_float(row["volume"])
            turnover = volume / shares * 100 if volume is not None and shares and shares > 0 else None
            industry = str(submissions.get("sicDescription") or identity.get("exchange") or "")
            description = str(submissions.get("description") or "")
            if not description and industry:
                description = f"SEC 行业分类：{industry}。"
            name = str(submissions.get("name") or identity.get("name") or symbol)
            history = histories.get(symbol, pd.DataFrame(columns=["date", "close", "volume"]))
            if history.empty:
                print(f"warning: Alpaca returned no history for {symbol}")
            short_interest = pd.DataFrame(columns=["date", "short_interest", "days_to_cover"]) if short_interest_all.empty else short_interest_all[short_interest_all["symbol"] == symbol][["date", "short_interest", "days_to_cover"]].copy()
            concepts = classify_concepts(f"{industry} {description}", rules)
            enriched[symbol] = {"symbol": symbol, "file_name": safe_file_name(symbol), "name": name, "close": close, "change_pct": clean_float(row["change_pct"]), "volume": volume, "dollar_volume": clean_float(row["dollar_volume"]), "market_cap": market_cap, "pe": pe, "eps": eps, "revenue": fundamentals.get("revenue"), "shares_outstanding": shares, "turnover_pct": turnover, "industry": industry, "description": description, "concepts": concepts, "cik": str(cik) if cik else None, "ownership": ownership, "history": history, "short_interest": short_interest}

        print("Downloading FINRA short-volume history …")
        finra = finra_client.history(selected_symbols, history_start, config.report_date)
        for symbol, stock in enriched.items():
            stock["short_volume"] = finra[finra["symbol"] == symbol].copy() if not finra.empty else pd.DataFrame()
        gainers = [enriched[symbol] for symbol in gainers_base["symbol"].astype(str) if symbol in enriched]
        losers = [enriched[symbol] for symbol in losers_base["symbol"].astype(str) if symbol in enriched]
        return {"indices": indices, "gainers": gainers, "losers": losers, "finra": finra, "short_interest": short_interest_all}

    def write_outputs(config: Any, payload: dict[str, Any]) -> None:
        original_write_outputs(config, payload)
        if config.mode != "live":
            return
        metadata_file = config.output / "metadata.json"
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        metadata["sources"] = ["Massive grouped daily and batched short interest", "Alpaca batch historical bars", "FRED", "SEC EDGAR", "FINRA"]
        metadata_file.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    namespace["live_payload"] = live_payload
    namespace["write_outputs"] = write_outputs
