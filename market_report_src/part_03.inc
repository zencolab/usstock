        return {"revenue": revenue, "eps": eps, "shares_outstanding": shares}

    @staticmethod
    def ownership_summary(payload: dict[str, Any], cik: str | int | None) -> dict[str, Any]:
        padded = SecClient._cik(cik)
        recent = (((payload.get("filings") or {}).get("recent")) or {})
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        filed = recent.get("filingDate") or []
        docs = recent.get("primaryDocument") or []
        filings: list[dict[str, str]] = []
        beneficial = 0
        insider = 0
        cutoff = date.today() - timedelta(days=365)
        for i, form in enumerate(forms):
            form_s = str(form).upper()
            if not (form_s.startswith("SC 13D") or form_s.startswith("SC 13G") or form_s in {"3", "3/A", "4", "4/A", "5", "5/A"}):
                continue
            filing_date = str(filed[i]) if i < len(filed) else ""
            try:
                filing_day = date.fromisoformat(filing_date)
            except ValueError:
                filing_day = cutoff
            if form_s.startswith("SC 13"):
                beneficial += 1
            if form_s in {"3", "3/A", "4", "4/A", "5", "5/A"} and filing_day >= date.today() - timedelta(days=90):
                insider += 1
            if len(filings) < 8 and padded and i < len(accessions):
                accession = str(accessions[i])
                accession_clean = accession.replace("-", "")
                primary = str(docs[i]) if i < len(docs) else ""
                cik_int = str(int(padded))
                url = f"{SEC_ARCHIVES}/{cik_int}/{accession_clean}/{primary}" if primary else "https://www.sec.gov/edgar/search/"
                filings.append({"form": form_s, "filing_date": filing_date, "url": url})
        summary = f"最近申报列表含 {beneficial} 份 13D/13G 受益所有权文件；近 90 天含 {insider} 份 Form 3/4/5 内部人申报。"
        return {"summary": summary, "filings": filings}


class FinraClient:
    def __init__(self, http: HttpClient, cache_dir: Path) -> None:
        self.http = http
        self.cache_dir = cache_dir / "finra"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _download_day(self, day: date, symbols: set[str]) -> list[dict[str, Any]]:
        cache_file = self.cache_dir / f"CNMSshvol{day:%Y%m%d}.txt"
        text = ""
        if cache_file.exists():
            text = cache_file.read_text(encoding="utf-8", errors="replace")
        else:
            url = FINRA_DAILY.format(day=day.strftime("%Y%m%d"))
            try:
                text = self.http.get_text(url, timeout=30)
                cache_file.write_text(text, encoding="utf-8")
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    return []
                print(f"warning: FINRA file unavailable for {day}: {exc}")
                return []
            except requests.RequestException as exc:
                print(f"warning: FINRA file unavailable for {day}: {exc}")
                return []
        rows: list[dict[str, Any]] = []
        reader = csv.DictReader(io.StringIO(text), delimiter="|")
        for row in reader:
            symbol = str(row.get("Symbol") or "").strip().upper()
            if symbol not in symbols:
                continue
            try:
                short_volume = int(float(row.get("ShortVolume") or 0))
                exempt_volume = int(float(row.get("ShortExemptVolume") or 0))
                total_volume = int(float(row.get("TotalVolume") or 0))
            except ValueError:
                continue
            rows.append({
                "date": day,
                "symbol": symbol,
                "short_volume": short_volume,
                "short_exempt_volume": exempt_volume,
                "total_volume": total_volume,
                "short_ratio": short_volume / total_volume if total_volume else None,
            })
        return rows

    def history(self, symbols: Iterable[str], start: date, end: date) -> pd.DataFrame:
        wanted = set(symbols)
        days = [d.date() for d in pd.bdate_range(start=start, end=end)]
        all_rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(self._download_day, day, wanted): day for day in days}
            for future in as_completed(futures):
                try:
                    all_rows.extend(future.result())
                except Exception as exc:
                    print(f"warning: FINRA processing failed for {futures[future]}: {exc}")
        if not all_rows:
            return pd.DataFrame(columns=["date", "symbol", "short_volume", "short_exempt_volume", "total_volume", "short_ratio"])
        return pd.DataFrame(all_rows).sort_values(["symbol", "date"])


def resolve_trade_dates(requested: str | None) -> tuple[date, date]:
    if mcal is None:
        # Used only by the no-network demo in a minimal environment.
        # Production installs pandas-market-calendars and validates NYSE holidays.
        target = date.fromisoformat(requested) if requested else datetime.now(ZoneInfo("America/New_York")).date()
        sessions = [ts.date() for ts in pd.bdate_range(end=target, periods=10)]
        if target not in sessions:
            target = sessions[-1]
        previous = max(d for d in sessions if d < target)
        return target, previous

