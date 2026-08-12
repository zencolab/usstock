            return pd.DataFrame(columns=["date", "short_interest", "days_to_cover"])
        rows = payload.get("results") or []
        frame = pd.DataFrame(rows)
        if frame.empty:
            return pd.DataFrame(columns=["date", "short_interest", "days_to_cover"])
        date_col = next((c for c in ["settlement_date", "settlementDate", "date"] if c in frame.columns), None)
        value_col = next((c for c in ["short_interest", "shortInterest", "current_short_position"] if c in frame.columns), None)
        days_col = next((c for c in ["days_to_cover", "daysToCover"] if c in frame.columns), None)
        if not date_col or not value_col:
            return pd.DataFrame(columns=["date", "short_interest", "days_to_cover"])
        out = pd.DataFrame({
            "date": pd.to_datetime(frame[date_col], errors="coerce").dt.date,
            "short_interest": pd.to_numeric(frame[value_col], errors="coerce"),
        })
        out["days_to_cover"] = pd.to_numeric(frame[days_col], errors="coerce") if days_col else np.nan
        return out.dropna(subset=["date", "short_interest"]).sort_values("date")


class SecClient:
    def __init__(self, http: HttpClient) -> None:
        self.http = http

    @staticmethod
    def _cik(cik: str | int | None) -> str | None:
        if cik is None or str(cik).strip() == "":
            return None
        digits = re.sub(r"\D", "", str(cik))
        return digits.zfill(10) if digits else None

    def company_facts(self, cik: str | int | None) -> dict[str, Any]:
        padded = self._cik(cik)
        if not padded:
            return {}
        try:
            return self.http.get_json(f"{SEC_BASE}/api/xbrl/companyfacts/CIK{padded}.json")
        except requests.HTTPError as exc:
            print(f"warning: SEC company facts unavailable for CIK {padded}: {exc}")
            return {}

    def submissions(self, cik: str | int | None) -> dict[str, Any]:
        padded = self._cik(cik)
        if not padded:
            return {}
        try:
            return self.http.get_json(f"{SEC_BASE}/submissions/CIK{padded}.json")
        except requests.HTTPError as exc:
            print(f"warning: SEC submissions unavailable for CIK {padded}: {exc}")
            return {}

    @staticmethod
    def latest_annual_fact(
        payload: dict[str, Any],
        tags: list[tuple[str, str]],
        units: Iterable[str],
    ) -> float | None:
        facts = payload.get("facts") or {}
        candidates: list[dict[str, Any]] = []
        for namespace, tag in tags:
            node = ((facts.get(namespace) or {}).get(tag) or {}).get("units") or {}
            for unit in units:
                for item in node.get(unit) or []:
                    if item.get("form") not in {"10-K", "10-K/A", "20-F", "20-F/A"}:
                        continue
                    if item.get("val") is None:
                        continue
                    start, end = item.get("start"), item.get("end")
                    if start and end:
                        try:
                            duration = (pd.Timestamp(end) - pd.Timestamp(start)).days
                            if duration < 300 or duration > 430:
                                continue
                        except Exception:
                            pass
                    candidates.append(item)
        if not candidates:
            return None
        candidates.sort(key=lambda x: (str(x.get("filed", "")), str(x.get("end", ""))), reverse=True)
        try:
            return float(candidates[0]["val"])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def latest_instant_fact(
        payload: dict[str, Any],
        tags: list[tuple[str, str]],
        units: Iterable[str],
    ) -> float | None:
        facts = payload.get("facts") or {}
        candidates: list[dict[str, Any]] = []
        for namespace, tag in tags:
            node = ((facts.get(namespace) or {}).get(tag) or {}).get("units") or {}
            for unit in units:
                candidates.extend([item for item in (node.get(unit) or []) if item.get("val") is not None])
        if not candidates:
            return None
        candidates.sort(key=lambda x: (str(x.get("filed", "")), str(x.get("end", ""))), reverse=True)
        try:
            return float(candidates[0]["val"])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def fundamentals(payload: dict[str, Any]) -> dict[str, float | None]:
        revenue = SecClient.latest_annual_fact(
            payload,
            [
                ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
                ("us-gaap", "Revenues"),
                ("us-gaap", "SalesRevenueNet"),
                ("ifrs-full", "Revenue"),
            ],
            ["USD"],
        )
        eps = SecClient.latest_annual_fact(
            payload,
            [
                ("us-gaap", "EarningsPerShareDiluted"),
                ("us-gaap", "EarningsPerShareBasicAndDiluted"),
                ("ifrs-full", "DilutedEarningsLossPerShare"),
            ],
            ["USD/shares", "USD / shares"],
        )
        shares = SecClient.latest_instant_fact(
            payload,
            [
                ("dei", "EntityCommonStockSharesOutstanding"),
                ("us-gaap", "CommonStockSharesOutstanding"),
            ],
            ["shares"],
        )
