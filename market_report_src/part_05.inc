        f'<svg viewBox="0 0 {int(width)} {int(height)}" role="img" aria-label="{html.escape(aria_label)}" xmlns="http://www.w3.org/2000/svg">',
        '<style>text{font:12px -apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;fill:var(--muted)} .hover{opacity:.01} .hover:hover{opacity:.18}</style>',
    ]
    for step in range(5):
        value = y_min + (y_max - y_min) * step / 4
        y = y_pos(value)
        pieces.append(f'<line x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}" stroke="var(--border)" stroke-width="1"/>')
        pieces.append(f'<text x="{left-8}" y="{y+4:.2f}" text-anchor="end">{html.escape(compact_number(value))}</text>')

    if kind == "bar":
        slot = plot_w / len(clean)
        bar_width = max(1.2, slot * 0.72)
        baseline = y_pos(0)
        for i, (_, value, tip) in enumerate(clean):
            x = left + slot * i + (slot - bar_width) / 2
            y = y_pos(value)
            h = max(0.8, baseline - y)
            pieces.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{h:.2f}" rx="1.5" fill="{semantic_color}"><title>{html.escape(tip)}</title></rect>'
            )
    else:
        points = [(x_pos(i), y_pos(value)) for i, (_, value, _) in enumerate(clean)]
        path = " ".join(("M" if i == 0 else "L") + f" {x:.2f} {y:.2f}" for i, (x, y) in enumerate(points))
        pieces.append(f'<path d="{path}" fill="none" stroke="{semantic_color}" stroke-width="2.6" stroke-linejoin="round" stroke-linecap="round"/>')
        radius = 3.4 if len(points) <= 40 else 2.2
        for (x, y), (_, _, tip) in zip(points, clean):
            pieces.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius}" fill="{semantic_color}"><title>{html.escape(tip)}</title></circle>')

    label_indexes = sorted(set([0, len(clean) // 2, len(clean) - 1]))
    for i in label_indexes:
        anchor = "start" if i == 0 else "end" if i == len(clean) - 1 else "middle"
        pieces.append(f'<text x="{x_pos(i):.2f}" y="{height-8}" text-anchor="{anchor}">{html.escape(clean[i][0])}</text>')
    pieces.append("</svg>")
    return Markup("".join(pieces))


def make_demo_series(end: date, *, start_value: float, drift: float, volatility: float, seed: int, periods: int = 126) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(end=end, periods=periods)
    returns = rng.normal(drift, volatility, periods)
    values = start_value * np.exp(np.cumsum(returns))
    volumes = rng.integers(500_000, 8_000_000, periods)
    return pd.DataFrame({"date": days.date, "close": values, "volume": volumes})


def demo_payload(config: RunConfig) -> dict[str, Any]:
    rules = load_concepts()
    index_specs = [
        ("NASDAQCOM", "纳斯达克综合指数", 17800, 0.0005, 0.010, 11),
        ("DJIA", "道琼斯工业指数", 42000, 0.0003, 0.006, 22),
        ("SP500", "标普 500", 5600, 0.0004, 0.007, 33),
    ]
    indices = []
    for series_id, name, start_value, drift, vol, seed in index_specs:
        frame = make_demo_series(config.report_date, start_value=start_value, drift=drift, volatility=vol, seed=seed)
        change = (frame.iloc[-1]["close"] / frame.iloc[-2]["close"] - 1) * 100
        indices.append({
            "series_id": series_id,
            "name": name,
            "close": float(frame.iloc[-1]["close"]),
            "change_pct": float(change),
            "history": frame,
        })

    industries = [
        ("Software—Infrastructure", "Develops cloud software and artificial intelligence platforms for enterprise customers."),
        ("Semiconductors", "Designs semiconductor and integrated circuit products for data centers and edge devices."),
        ("Biotechnology", "Develops biotechnology and gene therapy treatments for rare diseases."),
        ("Auto Manufacturers", "Builds electric vehicle platforms, batteries and charging technology."),
        ("Cybersecurity", "Provides cybersecurity, endpoint security and identity protection software."),
        ("Renewable Energy", "Develops solar power, renewable energy and grid-scale energy storage projects."),
    ]
    gainers: list[dict[str, Any]] = []
    losers: list[dict[str, Any]] = []
    all_short_rows: list[pd.DataFrame] = []
    all_interest_rows: list[pd.DataFrame] = []

    for direction, bucket in [(1, gainers), (-1, losers)]:
        for idx in range(config.top_n):
            code = f"{'UP' if direction > 0 else 'DN'}{idx+1:03d}"
            industry, description = industries[idx % len(industries)]
            seed = (1000 if direction > 0 else 2000) + idx
            history = make_demo_series(
                config.report_date,
                start_value=20 + idx * 2.2,
                drift=0.0002 * direction,
                volatility=0.018 + (idx % 4) * 0.002,
                seed=seed,
            )
            change_pct = direction * (28.0 - idx * (18.0 / max(config.top_n, 1)))
            history.loc[history.index[-1], "close"] = history.iloc[-2]["close"] * (1 + change_pct / 100)
            close = float(history.iloc[-1]["close"])
            volume = int(2_000_000 + idx * 180_000)
            shares = float(45_000_000 + idx * 3_500_000)
            eps = float(0.8 + (idx % 7) * 0.35)
            market_cap = close * shares
            revenue = float(850_000_000 + idx * 125_000_000)
            dates = pd.bdate_range(end=config.report_date, periods=90).date
            rng = np.random.default_rng(seed + 99)
