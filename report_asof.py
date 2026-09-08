"""Point-in-time (as-of) SEC helpers.

The original helpers picked the *latest currently available* filing and used
``date.today()`` for the insider window. Regenerating an older report therefore
mixed in disclosures that did not exist on the report date, so a backfilled
report could not be treated as a point-in-time record.

Every function here takes an explicit ``as_of`` date and ignores anything filed
after it. ``as_of=None`` keeps the old "latest available" behaviour so callers
that genuinely want today's data are unchanged.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "20-F", "20-F/A"})
BENEFICIAL_OWNERSHIP_FORMS = frozenset(
    {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
)
INSIDER_FORMS = frozenset({"4", "4/A"})

DEFAULT_OWNERSHIP_LOOKBACK_DAYS = 365
DEFAULT_INSIDER_LOOKBACK_DAYS = 90


def coerce_date(value: Any) -> date | None:
    """Parse an SEC date string (or date) into a ``date``."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def fact_known_on(item: Mapping[str, Any]) -> date | None:
    """Date a company fact became public knowledge.

    XBRL company facts carry ``filed`` (the accepted filing date). When it is
    missing we fall back to the period ``end``, which is never later than the
    filing date, so the fallback stays conservative.
    """

    return coerce_date(item.get("filed")) or coerce_date(item.get("end"))


def is_known_by(item: Mapping[str, Any], as_of: date | None) -> bool:
    """True when ``item`` was already public on ``as_of``."""

    if as_of is None:
        return True
    known = fact_known_on(item)
    # A fact with no usable date cannot be proven to predate as_of.
    return known is not None and known <= as_of


def _sorted_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(
        candidates,
        key=lambda x: (str(x.get("filed", "")), str(x.get("end", ""))),
        reverse=True,
    )


def latest_annual_fact(
    payload: Mapping[str, Any],
    tags: Sequence[tuple[str, str]],
    units: Iterable[str],
    *,
    as_of: date | None = None,
) -> float | None:
    """Latest annual XBRL fact that was public on ``as_of``."""

    facts = payload.get("facts") or {}
    units = list(units)
    candidates: list[Mapping[str, Any]] = []
    for namespace, tag in tags:
        node = ((facts.get(namespace) or {}).get(tag) or {}).get("units") or {}
        for unit in units:
            for item in node.get(unit) or []:
                if item.get("form") not in ANNUAL_FORMS:
                    continue
                if item.get("val") is None:
                    continue
                if not is_known_by(item, as_of):
                    continue
                start, end = coerce_date(item.get("start")), coerce_date(item.get("end"))
                if start and end:
                    duration = (end - start).days
                    if duration < 300 or duration > 430:
                        continue
                candidates.append(item)
    for item in _sorted_candidates(candidates):
        try:
            return float(item["val"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def latest_instant_fact(
    payload: Mapping[str, Any],
    tags: Sequence[tuple[str, str]],
    units: Iterable[str],
    *,
    as_of: date | None = None,
) -> float | None:
    """Latest point-in-time XBRL fact that was public on ``as_of``."""

    facts = payload.get("facts") or {}
    units = list(units)
    candidates: list[Mapping[str, Any]] = []
    for namespace, tag in tags:
        node = ((facts.get(namespace) or {}).get(tag) or {}).get("units") or {}
        for unit in units:
            candidates.extend(
                item
                for item in (node.get(unit) or [])
                if item.get("val") is not None and is_known_by(item, as_of)
            )
    for item in _sorted_candidates(candidates):
        try:
            return float(item["val"])
        except (KeyError, TypeError, ValueError):
            continue
    return None


REVENUE_TAGS = [
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("us-gaap", "Revenues"),
    ("us-gaap", "SalesRevenueNet"),
    ("ifrs-full", "Revenue"),
]
EPS_TAGS = [
    ("us-gaap", "EarningsPerShareDiluted"),
    ("us-gaap", "EarningsPerShareBasicAndDiluted"),
    ("ifrs-full", "DilutedEarningsLossPerShare"),
]
SHARES_TAGS = [
    ("dei", "EntityCommonStockSharesOutstanding"),
    ("us-gaap", "CommonStockSharesOutstanding"),
]


def fundamentals(
    payload: Mapping[str, Any], *, as_of: date | None = None
) -> dict[str, float | None]:
    """Revenue / EPS / shares outstanding as disclosed on ``as_of``."""

    return {
        "revenue": latest_annual_fact(payload, REVENUE_TAGS, ["USD"], as_of=as_of),
        "eps": latest_annual_fact(
            payload, EPS_TAGS, ["USD/shares", "USD / shares"], as_of=as_of
        ),
        "shares_outstanding": latest_instant_fact(
            payload, SHARES_TAGS, ["shares"], as_of=as_of
        ),
    }


def _filing_url(cik: str | int | None, accession: str, document: str) -> str:
    digits = "".join(ch for ch in str(cik or "") if ch.isdigit()).lstrip("0") or "0"
    folder = accession.replace("-", "")
    if document:
        return f"{SEC_ARCHIVES}/{digits}/{folder}/{document}"
    return f"{SEC_ARCHIVES}/{digits}/{folder}"


def recent_filings(
    submissions: Mapping[str, Any],
    *,
    as_of: date | None = None,
    forms: Iterable[str] | None = None,
    lookback_days: int = DEFAULT_OWNERSHIP_LOOKBACK_DAYS,
    cik: str | int | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Ownership-related filings visible on ``as_of``, newest first."""

    recent = ((submissions.get("filings") or {}).get("recent")) or {}
    form_values = list(recent.get("form") or [])
    dates = list(recent.get("filingDate") or [])
    accessions = list(recent.get("accessionNumber") or [])
    documents = list(recent.get("primaryDocument") or [])
    allowed = set(forms) if forms is not None else (
        BENEFICIAL_OWNERSHIP_FORMS | INSIDER_FORMS
    )
    effective_cik = cik if cik is not None else submissions.get("cik")
    cutoff = None if as_of is None else as_of - timedelta(days=lookback_days)

    rows: list[dict[str, Any]] = []
    for index, form in enumerate(form_values):
        form = str(form).strip()
        if form not in allowed:
            continue
        filing_day = coerce_date(dates[index] if index < len(dates) else None)
        if filing_day is None:
            continue
        # The core point-in-time rule: nothing filed after the report date.
        if as_of is not None and filing_day > as_of:
            continue
        if cutoff is not None and filing_day < cutoff:
            continue
        accession = str(accessions[index]) if index < len(accessions) else ""
        document = str(documents[index]) if index < len(documents) else ""
        rows.append(
            {
                "form": form,
                "filing_date": filing_day.isoformat(),
                "url": _filing_url(effective_cik, accession, document),
            }
        )
    rows.sort(key=lambda row: row["filing_date"], reverse=True)
    return rows[:limit]


def ownership_summary(
    submissions: Mapping[str, Any],
    cik: str | int | None = None,
    *,
    as_of: date | None = None,
    lookback_days: int = DEFAULT_OWNERSHIP_LOOKBACK_DAYS,
    insider_days: int = DEFAULT_INSIDER_LOOKBACK_DAYS,
) -> dict[str, Any]:
    """Beneficial-ownership / insider filing summary as of ``as_of``.

    ``as_of`` replaces the previous ``date.today()`` reference, so the insider
    window is measured against the report date rather than the run date.
    """

    filings = recent_filings(
        submissions,
        as_of=as_of,
        lookback_days=lookback_days,
        cik=cik,
    )
    beneficial = [row for row in filings if row["form"] in BENEFICIAL_OWNERSHIP_FORMS]
    insider_cutoff = None if as_of is None else as_of - timedelta(days=insider_days)
    insider = [
        row
        for row in filings
        if row["form"] in INSIDER_FORMS
        and (
            insider_cutoff is None
            or coerce_date(row["filing_date"]) >= insider_cutoff
        )
    ]

    if not filings:
        summary = (
            "最近申报列表中没有匹配的 13D/13G 或 Form 4 记录"
            if as_of is None
            else f"截至 {as_of.isoformat()}，最近申报列表中没有匹配的 13D/13G 或 Form 4 记录"
        )
    else:
        parts = [f"受益所有权申报（13D/13G）{len(beneficial)} 条"]
        parts.append(f"最近 {insider_days} 天内部人申报（Form 4）{len(insider)} 条")
        prefix = "" if as_of is None else f"截至 {as_of.isoformat()}："
        summary = prefix + "，".join(parts) + "。仅为申报摘要，不是完整机构持仓榜。"

    return {
        "summary": summary,
        "filings": filings,
        "beneficial_count": len(beneficial),
        "insider_count": len(insider),
        "as_of": None if as_of is None else as_of.isoformat(),
        "insider_days": insider_days,
    }
