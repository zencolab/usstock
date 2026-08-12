                short_volume["short_volume"].tolist(),
                aria_label=f"{stock['symbol']}日度卖空成交量",
                kind="bar",
                semantic="negative",
                tooltips=tips,
            )
        else:
            stock["short_volume_chart"] = _chart_empty("暂无 FINRA 日度卖空数据")

        short_interest = stock.get("short_interest")
        if isinstance(short_interest, pd.DataFrame) and not short_interest.empty:
            tips = [
                f"{row.date}: 空头持仓 {compact_number(row.short_interest)}，Days to cover {getattr(row, 'days_to_cover', float('nan')):.2f}"
                for row in short_interest.itertuples()
            ]
            stock["short_interest_chart"] = svg_chart(
                short_interest["date"].tolist(),
                short_interest["short_interest"].tolist(),
                aria_label=f"{stock['symbol']}空头持仓数量",
                kind="line",
                semantic="accent",
                tooltips=tips,
            )
        else:
            stock["short_interest_chart"] = _chart_empty("暂无空头持仓数据")


def public_stock_record(stock: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "symbol", "file_name", "name", "close", "change_pct", "volume", "dollar_volume", "market_cap", "pe", "eps",
        "revenue", "shares_outstanding", "turnover_pct", "industry", "description", "concepts", "cik", "ownership",
    ]
    return {key: stock.get(key) for key in keys}


def write_outputs(config: RunConfig, payload: dict[str, Any]) -> None:
    site = config.output
    data_dir = config.data_output / config.report_date.isoformat()
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True, exist_ok=True)
    (site / "assets").mkdir(parents=True, exist_ok=True)
    (site / "stocks").mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(TEMPLATES / "style.css", site / "assets" / "style.css")

    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update({
        "money": money,
        "number": number,
        "integer": integer,
        "ratio": ratio,
        "pct": pct,
        "pct_plain": pct_plain,
    })
    context = {
        "report_date": config.report_date.isoformat(),
        "generated_at": datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M %Z"),
        "mode": config.mode,
        "top_n": config.top_n,
        "min_price": config.min_price,
        "min_dollar_volume": config.min_dollar_volume,
        "indices": payload["indices"],
        "gainers": payload["gainers"],
        "losers": payload["losers"],
    }
    index_html = env.get_template("index.html.j2").render(**context)
    (site / "index.html").write_text(index_html, encoding="utf-8")
    stock_template = env.get_template("stock.html.j2")
    seen: set[str] = set()
    for stock in payload["gainers"] + payload["losers"]:
        if stock["symbol"] in seen:
            continue
        seen.add(stock["symbol"])
        rendered = stock_template.render(report_date=config.report_date.isoformat(), stock=stock)
        (site / "stocks" / f"{stock['file_name']}.html").write_text(rendered, encoding="utf-8")

    metadata = {
        "report_date": config.report_date.isoformat(),
        "previous_date": config.previous_date.isoformat(),
        "mode": config.mode,
        "top_n": config.top_n,
        "generated_at": datetime.now().astimezone().isoformat(),
        "sources": ["Massive", "FRED", "SEC EDGAR", "FINRA"] if config.mode == "live" else ["deterministic demo fixtures"],
    }
    (site / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    index_rows = []
    for item in payload["indices"]:
        history = item["history"].copy()
        history["series_id"] = item["series_id"]
        history["name"] = item["name"]
        index_rows.append(history[["date", "series_id", "name", "close"]])
    if index_rows:
        pd.concat(index_rows, ignore_index=True).to_csv(data_dir / "indices.csv", index=False)

    columns = ["symbol", "name", "close", "change_pct", "volume", "dollar_volume", "market_cap", "pe", "eps", "revenue", "shares_outstanding", "turnover_pct", "industry", "concepts"]
    for name, rows in [("gainers", payload["gainers"]), ("losers", payload["losers"])]:
        frame = pd.DataFrame([{**public_stock_record(row), "concepts": "|".join(row.get("concepts") or [])} for row in rows])
        if frame.empty:
            frame = pd.DataFrame(columns=columns)
        frame.to_csv(data_dir / f"{name}.csv", index=False)

    details = [public_stock_record(stock) for stock in payload["gainers"] + payload["losers"]]
    (data_dir / "stock_details.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    finra = payload.get("finra")
    if isinstance(finra, pd.DataFrame):
        finra.to_csv(data_dir / "finra_short_volume.csv", index=False)
    short_interest = payload.get("short_interest")
    if isinstance(short_interest, pd.DataFrame):
        short_interest.to_csv(data_dir / "short_interest.csv", index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the US market close report")
