        print(f"[{position+1}/{len(selected)}] enriching {symbol}")
        try:
            history = massive.history(symbol, start, config.report_date)
        except Exception as exc:
            print(f"warning: price history unavailable for {symbol}: {exc}")
            history = pd.DataFrame(columns=["date", "close", "volume"])
        details = ticker_cache.get(symbol)
        if not isinstance(details, dict) or not details:
            try:
                details = massive.ticker_details(symbol)
                ticker_cache[symbol] = details
            except Exception as exc:
                print(f"warning: ticker details unavailable for {symbol}: {exc}")
                details = {}
        try:
            short_interest = massive.short_interest(symbol)
        except Exception as exc:
            print(f"warning: short interest unavailable for {symbol}: {exc}")
            short_interest = pd.DataFrame(columns=["date", "short_interest", "days_to_cover"])
        if not short_interest.empty:
            copy = short_interest.copy()
            copy["symbol"] = symbol
            interest_frames.append(copy)

        cik = details.get("cik")
        facts_payload = sec.company_facts(cik) if cik else {}
        fundamentals = sec.fundamentals(facts_payload) if facts_payload else {"revenue": None, "eps": None, "shares_outstanding": None}
        submissions = sec.submissions(cik) if cik else {}
        ownership = sec.ownership_summary(submissions, cik) if submissions else {"summary": "SEC 最近申报数据不可用。", "filings": []}
        shares = fundamentals.get("shares_outstanding") or clean_float(details.get("share_class_shares_outstanding")) or clean_float(details.get("weighted_shares_outstanding"))
        market_cap = clean_float(details.get("market_cap")) or (float(row["close"]) * shares if shares else None)
        eps = fundamentals.get("eps")
        pe = float(row["close"]) / eps if eps is not None and eps > 0 else None
        turnover = float(row["volume"]) / shares * 100 if shares and shares > 0 else None
        industry = str(details.get("sic_description") or details.get("type") or "")
        description = str(details.get("description") or "")
        concepts = classify_concepts(f"{industry} {description}", rules)
        enriched[symbol] = {
            "symbol": symbol,
            "file_name": safe_file_name(symbol),
            "name": str(details.get("name") or symbol),
            "close": clean_float(row["close"]),
            "change_pct": clean_float(row["change_pct"]),
            "volume": clean_float(row["volume"]),
            "dollar_volume": clean_float(row["dollar_volume"]),
            "market_cap": market_cap,
            "pe": pe,
            "eps": eps,
            "revenue": fundamentals.get("revenue"),
            "shares_outstanding": shares,
            "turnover_pct": turnover,
            "industry": industry,
            "description": description,
            "concepts": concepts,
            "cik": str(cik) if cik else None,
            "ownership": ownership,
            "history": history,
            "short_interest": short_interest,
        }

    ticker_cache_path.write_text(json.dumps(ticker_cache, ensure_ascii=False), encoding="utf-8")
    print("Downloading FINRA short-volume history …")
    finra = finra_client.history(selected_symbols, start, config.report_date)
    for symbol, stock in enriched.items():
        stock["short_volume"] = finra[finra["symbol"] == symbol].copy() if not finra.empty else pd.DataFrame()

    gainers = [enriched[symbol] for symbol in gainers_base["symbol"].astype(str) if symbol in enriched]
    losers = [enriched[symbol] for symbol in losers_base["symbol"].astype(str) if symbol in enriched]
    return {
        "indices": indices,
        "gainers": gainers,
        "losers": losers,
        "finra": finra,
        "short_interest": pd.concat(interest_frames, ignore_index=True) if interest_frames else pd.DataFrame(),
    }


def add_charts(payload: dict[str, Any]) -> None:
    for item in payload["indices"]:
        frame = item["history"]
        semantic = "positive" if (item.get("change_pct") or 0) >= 0 else "negative"
        item["chart"] = svg_chart(
            frame.get("date", pd.Series(dtype=object)).tolist(),
            frame.get("close", pd.Series(dtype=float)).tolist(),
            aria_label=f"{item['name']}近半年走势",
            kind="line",
            semantic=semantic,
        )

    for stock in payload["gainers"] + payload["losers"]:
        history = stock.get("history")
        semantic = "positive" if (stock.get("change_pct") or 0) >= 0 else "negative"
        if isinstance(history, pd.DataFrame) and not history.empty:
            stock["price_chart"] = svg_chart(
                history["date"].tolist(), history["close"].tolist(), aria_label=f"{stock['symbol']}近半年价格", kind="line", semantic=semantic
            )
        else:
            stock["price_chart"] = _chart_empty("暂无价格历史")

        short_volume = stock.get("short_volume")
        if isinstance(short_volume, pd.DataFrame) and not short_volume.empty:
            tips = [
                f"{row.date}: 卖空 {compact_number(row.short_volume)}，占 FINRA 总量 {row.short_ratio*100:.1f}%"
                for row in short_volume.itertuples()
            ]
            stock["short_volume_chart"] = svg_chart(
                short_volume["date"].tolist(),
