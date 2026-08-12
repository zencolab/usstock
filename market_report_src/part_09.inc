    parser.add_argument("--mode", choices=["live", "demo"], default="live")
    parser.add_argument("--date", help="Trading date in YYYY-MM-DD; default is latest completed NYSE session")
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--min-price", type=float, default=1.0)
    parser.add_argument("--min-dollar-volume", type=float, default=1_000_000.0)
    parser.add_argument("--output", type=Path, default=Path("site"))
    parser.add_argument("--data-output", type=Path, default=Path("output"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.top_n < 1 or args.top_n > 500:
        raise ValueError("--top-n must be between 1 and 500")
    report_date, previous_date = resolve_trade_dates(args.date)
    config = RunConfig(
        mode=args.mode,
        report_date=report_date,
        previous_date=previous_date,
        top_n=args.top_n,
        months=args.months,
        min_price=args.min_price,
        min_dollar_volume=args.min_dollar_volume,
        output=args.output,
        data_output=args.data_output,
    )
    print(f"Generating {config.mode} report for {config.report_date} (previous session {config.previous_date})")
    payload = demo_payload(config) if config.mode == "demo" else live_payload(config)
    add_charts(payload)
    write_outputs(config, payload)
    print(f"Report ready: {config.output / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
