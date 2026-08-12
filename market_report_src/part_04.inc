    calendar = mcal.get_calendar("NYSE")
    if requested:
        target = date.fromisoformat(requested)
        schedule = calendar.schedule(start_date=target - timedelta(days=15), end_date=target)
        session_dates = [ts.date() for ts in schedule.index]
        if target not in session_dates:
            raise ValueError(f"{target} is not an NYSE trading session")
        previous = max(d for d in session_dates if d < target)
        return target, previous

    now_ny = datetime.now(ZoneInfo("America/New_York"))
    end_day = now_ny.date()
    schedule = calendar.schedule(start_date=end_day - timedelta(days=20), end_date=end_day)
    now_utc = pd.Timestamp.now(tz="UTC")
    completed = schedule[schedule["market_close"] <= now_utc]
    if completed.empty:
        raise RuntimeError("No completed NYSE session found")
    target = completed.index[-1].date()
    session_dates = [ts.date() for ts in schedule.index]
    previous = max(d for d in session_dates if d < target)
    return target, previous

def months_ago(day: date, months: int) -> date:
    return (pd.Timestamp(day) - pd.DateOffset(months=months)).date()


def fred_history(http: HttpClient, series_id: str, start: date, end: date) -> pd.DataFrame:
    text = http.get_text(
        "https://fred.stlouisfed.org/graph/fredgraph.csv",
        params={"id": series_id, "cosd": start.isoformat(), "coed": end.isoformat()},
    )
    frame = pd.read_csv(io.StringIO(text))
    if frame.empty:
        return pd.DataFrame(columns=["date", "close"])
    date_col = frame.columns[0]
    value_col = frame.columns[1]
    frame = frame.rename(columns={date_col: "date", value_col: "close"})
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    return frame.dropna().sort_values("date")


def clean_float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def safe_file_name(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", symbol).strip("-") or "stock"


def load_concepts() -> dict[str, list[str]]:
    return json.loads(CONCEPTS_FILE.read_text(encoding="utf-8"))


def classify_concepts(text: str, rules: dict[str, list[str]], limit: int = 5) -> list[str]:
    haystack = text.lower()
    matches = [name for name, words in rules.items() if any(word.lower() in haystack for word in words)]
    return matches[:limit]


def compact_number(value: float | int | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "—"
    absolute = abs(float(value))
    for divisor, suffix in [(1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")]:
        if absolute >= divisor:
            return f"{float(value) / divisor:.1f}{suffix}"
    return f"{float(value):,.0f}"


def money(value: float | int | None) -> str:
    rendered = compact_number(value)
    return rendered if rendered == "—" else f"${rendered}"


def number(value: float | int | None) -> str:
    return "—" if value is None or not math.isfinite(float(value)) else f"{float(value):,.2f}"


def integer(value: float | int | None) -> str:
    return "—" if value is None or not math.isfinite(float(value)) else f"{int(float(value)):,}"


def ratio(value: float | int | None) -> str:
    return "—" if value is None or not math.isfinite(float(value)) else f"{float(value):,.2f}"


def pct(value: float | int | None) -> str:
    return "—" if value is None or not math.isfinite(float(value)) else f"{float(value):+.2f}%"


def pct_plain(value: float | int | None) -> str:
    return "—" if value is None or not math.isfinite(float(value)) else f"{float(value):.2f}%"


def _chart_empty(label: str) -> Markup:
    return Markup(f'<div class="chart-empty">{html.escape(label)}</div>')


def svg_chart(
    dates: list[Any],
    values: list[Any],
    *,
    aria_label: str,
    kind: str = "line",
    semantic: str = "accent",
    tooltips: list[str] | None = None,
) -> Markup:
    clean: list[tuple[str, float, str]] = []
    for i, (d, v) in enumerate(zip(dates, values)):
        numeric = clean_float(v)
        if numeric is None:
            continue
        label = d.isoformat() if hasattr(d, "isoformat") else str(d)
        tip = tooltips[i] if tooltips and i < len(tooltips) else f"{label}: {compact_number(numeric)}"
        clean.append((label, numeric, tip))
    if len(clean) < 2:
        return _chart_empty("暂无足够数据")

    width, height = 760.0, 260.0
    left, right, top, bottom = 58.0, 16.0, 14.0, 34.0
    plot_w, plot_h = width - left - right, height - top - bottom
    vals = [v for _, v, _ in clean]
    if kind == "bar":
        y_min, y_max = 0.0, max(vals) * 1.08 if max(vals) else 1.0
    else:
        low, high = min(vals), max(vals)
        pad = (high - low) * 0.12 or max(abs(high) * 0.03, 1.0)
        y_min, y_max = low - pad, high + pad
    span = y_max - y_min or 1.0

    def x_pos(i: int) -> float:
        return left + (plot_w * i / max(len(clean) - 1, 1))

    def y_pos(v: float) -> float:
        return top + (y_max - v) / span * plot_h

    semantic_color = {"positive": "var(--positive)", "negative": "var(--negative)", "accent": "var(--accent)"}.get(semantic, "var(--accent)")
    pieces = [
