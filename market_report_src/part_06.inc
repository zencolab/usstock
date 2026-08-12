            short_df = pd.DataFrame({
                "date": dates,
                "symbol": code,
                "short_volume": rng.integers(100_000, 1_800_000, len(dates)),
                "total_volume": rng.integers(1_000_000, 6_000_000, len(dates)),
            })
            short_df["short_exempt_volume"] = (short_df["short_volume"] * 0.02).astype(int)
            short_df["short_ratio"] = short_df["short_volume"] / short_df["total_volume"]
            all_short_rows.append(short_df)
            interest_dates = pd.date_range(end=config.report_date, periods=12, freq="15D").date
            interest_df = pd.DataFrame({
                "date": interest_dates,
                "symbol": code,
                "short_interest": np.maximum(100_000, np.cumsum(rng.normal(0, 180_000, len(interest_dates))) + 4_000_000),
                "days_to_cover": rng.uniform(1.0, 6.0, len(interest_dates)),
            })
            all_interest_rows.append(interest_df)
            concepts = classify_concepts(f"{industry} {description}", rules)
            bucket.append({
                "symbol": code,
                "file_name": safe_file_name(code),
                "name": f"演示公司 {idx+1}",
                "close": close,
                "change_pct": change_pct,
                "volume": volume,
                "dollar_volume": close * volume,
                "market_cap": market_cap,
                "pe": close / eps,
                "eps": eps,
                "revenue": revenue,
                "shares_outstanding": shares,
                "turnover_pct": volume / shares * 100,
                "industry": industry,
                "description": description,
                "concepts": concepts,
                "cik": None,
                "ownership": {"summary": "演示数据：最近申报列表含 2 份受益所有权文件；近 90 天含 4 份内部人申报。", "filings": []},
                "history": history,
                "short_volume": short_df,
                "short_interest": interest_df,
            })
    return {
        "indices": indices,
        "gainers": gainers,
        "losers": losers,
        "finra": pd.concat(all_short_rows, ignore_index=True) if all_short_rows else pd.DataFrame(),
        "short_interest": pd.concat(all_interest_rows, ignore_index=True) if all_interest_rows else pd.DataFrame(),
    }


def load_json_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def live_payload(config: RunConfig) -> dict[str, Any]:
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MASSIVE_API_KEY is required in live mode")
    sec_user_agent = os.getenv("SEC_USER_AGENT", "").strip()
    if not sec_user_agent or "@" not in sec_user_agent:
        raise RuntimeError("SEC_USER_AGENT must identify the application and include a contact email")
    rpm = float(os.getenv("MASSIVE_RPM", "5"))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    http = HttpClient(sec_user_agent)
    massive = MassiveClient(api_key, rpm, http)
    sec = SecClient(http)
    finra_client = FinraClient(http, CACHE_DIR)
    rules = load_concepts()
    start = months_ago(config.report_date, config.months)

    indices = []
    for series_id, name in INDEX_SERIES:
        frame = fred_history(http, series_id, start, config.report_date)
        if frame.empty:
            indices.append({"series_id": series_id, "name": name, "close": None, "change_pct": None, "history": frame})
            continue
        latest = frame.iloc[-1]
        previous = frame.iloc[-2] if len(frame) > 1 else latest
        change_pct = (latest["close"] / previous["close"] - 1) * 100 if previous["close"] else None
        indices.append({"series_id": series_id, "name": name, "close": float(latest["close"]), "change_pct": clean_float(change_pct), "history": frame})

    today = massive.grouped_daily(config.report_date)
    previous = massive.grouped_daily(config.previous_date)[["symbol", "close"]].rename(columns={"close": "previous_close"})
    movers = today.merge(previous, on="symbol", how="inner")
    for col in ["close", "previous_close", "volume"]:
        movers[col] = pd.to_numeric(movers[col], errors="coerce")
    movers["change_pct"] = (movers["close"] / movers["previous_close"] - 1) * 100
    movers["dollar_volume"] = movers["close"] * movers["volume"]
    symbol_ok = movers["symbol"].astype(str).str.match(r"^[A-Z][A-Z0-9.]{0,7}$")
    movers = movers[
        symbol_ok
        & (movers["close"] >= config.min_price)
        & (movers["previous_close"] > 0)
        & (movers["dollar_volume"] >= config.min_dollar_volume)
        & movers["change_pct"].replace([np.inf, -np.inf], np.nan).notna()
    ].copy()
    gainers_base = movers.nlargest(config.top_n, "change_pct")
    losers_base = movers.nsmallest(config.top_n, "change_pct")
    selected = pd.concat([gainers_base, losers_base], ignore_index=True).drop_duplicates("symbol")
    selected_symbols = selected["symbol"].astype(str).tolist()

    ticker_cache_path = CACHE_DIR / "ticker_details.json"
    ticker_cache = load_json_cache(ticker_cache_path)
    enriched: dict[str, dict[str, Any]] = {}
    interest_frames: list[pd.DataFrame] = []

    for position, row in selected.reset_index(drop=True).iterrows():
        symbol = str(row["symbol"])
