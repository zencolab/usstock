"""Guarded, one-shot fixes for the defects found while reviewing f7953bf.

Every edit matches an exact anchor an exact number of times. If the source has
drifted, the script refuses to guess and stops with a precise error, so a
partially applied migration can never be committed.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str, count: int = 1) -> None:
    file = ROOT / path
    text = file.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"Refusing ambiguous edit: {path}: expected {count}, found {actual}")
    file.write_text(text.replace(old, new), encoding="utf-8")


def append_new(path: str, marker: str, text: str) -> None:
    file = ROOT / path
    current = file.read_text(encoding="utf-8")
    if marker in current:
        raise RuntimeError(f"Refusing to append twice: {path} already defines {marker}")
    file.write_text(current.rstrip() + "\n\n\n" + text.strip() + "\n", encoding="utf-8")


FACT_HELPERS = '''    @staticmethod
    def _fact_available(item: dict[str, Any], as_of: date) -> bool:
        """A fact counts only when it was filed and dated on or before the report date."""
        filed = str(item.get("filed") or "").strip()
        if not filed:
            return False
        try:
            if date.fromisoformat(filed[:10]) > as_of:
                return False
        except ValueError:
            return False
        end = str(item.get("end") or "").strip()
        if end:
            try:
                if date.fromisoformat(end[:10]) > as_of:
                    return False
            except ValueError:
                return False
        return True

    @staticmethod
    def _facts_available_on(payload: dict[str, Any], as_of: date | None) -> dict[str, Any]:
        """Drop XBRL facts that were not public yet on the report date."""
        if as_of is None:
            return payload
        facts = payload.get("facts") or {}
        kept_namespaces: dict[str, Any] = {}
        for namespace, tags in facts.items():
            kept_tags: dict[str, Any] = {}
            for tag, node in (tags or {}).items():
                units: dict[str, Any] = {}
                for unit, items in ((node or {}).get("units") or {}).items():
                    usable = [item for item in (items or []) if SecClient._fact_available(item, as_of)]
                    if usable:
                        units[unit] = usable
                if units:
                    kept_tags[tag] = {**(node or {}), "units": units}
            if kept_tags:
                kept_namespaces[namespace] = kept_tags
        return {**payload, "facts": kept_namespaces}

'''

RUSSELL_MAIN = '''_engine_parse_args = engine.parse_args


def _parse_args():
    """Keep Russell 2000 output inside this project instead of the repository root."""
    return _engine_parse_args(
        default_output=PROJECT_ROOT / "site",
        default_data_output=PROJECT_ROOT / "output",
    )


engine.parse_args = _parse_args
main = engine.main'''

SAFE_URL = '''def safe_external_url(value: Any) -> str:
    """Return an http(s) URL that is safe to place in an href, or an empty string."""
    text = str(value or "").strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"}:
        return ""
    if not parts.netloc or "@" in parts.netloc:
        return ""
    return text


'''

CANONICALIZE_GUARD = '''    text = url.strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if parts.scheme.lower() not in {"http", "https"}:
        return ""
    if not parts.netloc or "@" in parts.netloc:
        return ""'''

SNAPSHOT_VALIDATOR = '''def validate_snapshot_as_of(
    snapshot: UniverseSnapshot,
    report_date: date,
    *,
    historical: bool = False,
    grace_days: int = 7,
) -> None:
    """Reject constituents that are undated or newer than the reported session."""
    as_of = str(snapshot.as_of or "").strip()
    if not as_of:
        if historical:
            raise RuntimeError(
                "Historical constituents require an as-of date; set "
                "RUSSELL2000_CONSTITUENTS_AS_OF for a plain ticker list"
            )
        return
    try:
        snapshot_date = date.fromisoformat(as_of[:10])
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid constituent as-of date: {as_of}") from exc
    if snapshot_date > report_date + timedelta(days=grace_days):
        raise RuntimeError(
            f"Constituents are newer than the report date ({snapshot_date} > {report_date}); "
            "provide a dated historical constituent file"
        )'''

RUSSELL_GUARD = '''        grace_days = int(os.getenv("RUSSELL2000_CONSTITUENTS_GRACE_DAYS", "7"))
        override = os.getenv("RUSSELL2000_CONSTITUENTS_FILE", "").strip()
        historical = config.report_date < date.today() - timedelta(days=grace_days)
        if historical and not override:
            raise RuntimeError(
                "Historical reports require RUSSELL2000_CONSTITUENTS_FILE; the latest IWM "
                "holdings are not the constituents of an older session"
            )
        snapshot = load_russell2000_universe(project_root / ".cache" / "universe")
        validate_snapshot_as_of(
            snapshot,
            config.report_date,
            historical=historical,
            grace_days=grace_days,
        )'''

WORKFLOW_INPUTS = '''      top_n:
        description: "Number of Russell 2000 gainers and losers"
        required: true
        default: "100"
        type: string
      historical_constituents_file:
        description: "Authorized dated constituents file; required for historical trade dates"
        required: false
        type: string
      constituents_as_of:
        description: "Constituents as-of date (YYYY-MM-DD) for plain ticker lists"
        required: false
        type: string'''


def fix_template_escaping() -> None:
    for path in ("market_report_src/part_08.inc", "bilingual_runtime.py"):
        replace(path, 'autoescape=select_autoescape(["html", "xml"]),', "autoescape=True,")


def fix_feed_defaults() -> None:
    replace("hybrid_data.py", 'feed: str = "sip"', 'feed: str = "iex"')
    replace(
        "hybrid_data.py",
        'self.feed = feed.strip().lower() or "sip"',
        'self.feed = feed.strip().lower() or "iex"',
    )
    replace(
        "hybrid_runtime.py",
        'alpaca_feed = os.getenv("ALPACA_FEED", "sip").strip().lower() or "sip"',
        'alpaca_feed = os.getenv("ALPACA_FEED", "iex").strip().lower() or "iex"',
    )


def fix_output_defaults() -> None:
    replace(
        "market_report_src/part_08.inc",
        "def parse_args() -> argparse.Namespace:",
        "def parse_args(\n    *,\n    default_output: Path | None = None,\n    default_data_output: Path | None = None,\n) -> argparse.Namespace:",
    )
    replace(
        "market_report_src/part_09.inc",
        'default=Path("site")',
        'default=default_output if default_output is not None else Path("site")',
    )
    replace(
        "market_report_src/part_09.inc",
        'default=Path("output")',
        'default=default_data_output if default_data_output is not None else Path("output")',
    )
    replace("russell2000_market_report/main.py", "main = engine.main", RUSSELL_MAIN)


def fix_sec_point_in_time() -> None:
    replace(
        "market_report_src/part_02.inc",
        "    @staticmethod\n    def latest_annual_fact(",
        FACT_HELPERS + "    @staticmethod\n    def latest_annual_fact(",
    )
    replace(
        "market_report_src/part_02.inc",
        "    def fundamentals(payload: dict[str, Any]) -> dict[str, float | None]:\n        revenue = SecClient.latest_annual_fact(",
        "    def fundamentals(\n        payload: dict[str, Any],\n        *,\n        as_of: date | None = None,\n    ) -> dict[str, float | None]:\n        payload = SecClient._facts_available_on(payload, as_of)\n        revenue = SecClient.latest_annual_fact(",
    )
    replace(
        "market_report_src/part_03.inc",
        "    def ownership_summary(payload: dict[str, Any], cik: str | int | None) -> dict[str, Any]:",
        "    def ownership_summary(\n        payload: dict[str, Any],\n        cik: str | int | None,\n        *,\n        as_of: date | None = None,\n    ) -> dict[str, Any]:",
    )
    replace(
        "market_report_src/part_03.inc",
        "        cutoff = date.today() - timedelta(days=365)",
        "        reference_date = as_of or date.today()\n        cutoff = reference_date - timedelta(days=365)\n        insider_cutoff = reference_date - timedelta(days=90)",
    )
    replace(
        "market_report_src/part_03.inc",
        "            except ValueError:\n                filing_day = cutoff\n",
        "            except ValueError:\n                filing_day = cutoff\n            if filing_day > reference_date:\n                continue\n",
    )
    replace(
        "market_report_src/part_03.inc",
        "filing_day >= date.today() - timedelta(days=90)",
        "filing_day >= insider_cutoff",
    )
    replace(
        "market_report_src/part_07.inc",
        "sec.fundamentals(facts_payload)",
        "sec.fundamentals(facts_payload, as_of=config.report_date)",
    )
    replace(
        "market_report_src/part_07.inc",
        "sec.ownership_summary(submissions, cik)",
        "sec.ownership_summary(submissions, cik, as_of=config.report_date)",
    )
    replace(
        "hybrid_runtime.py",
        "sec_parser.fundamentals(facts_payload)",
        "sec_parser.fundamentals(facts_payload, as_of=config.report_date)",
    )
    replace(
        "hybrid_runtime.py",
        "sec_parser.ownership_summary(submissions, cik)",
        "sec_parser.ownership_summary(submissions, cik, as_of=config.report_date)",
    )


def fix_external_links() -> None:
    replace(
        "news_translation.py",
        "from typing import Any, Iterable",
        "from typing import Any, Iterable\nfrom urllib.parse import urlsplit",
    )
    replace(
        "news_translation.py",
        "def _plain_text(value: Any) -> str:",
        SAFE_URL + "def _plain_text(value: Any) -> str:",
    )
    replace(
        "premium_translation.py",
        "    INDUSTRY_OVERRIDES,",
        "    INDUSTRY_OVERRIDES,\n    safe_external_url,",
    )
    replace(
        "premium_translation.py",
        '"url": str(item.get("url") or ""),',
        '"url": safe_external_url(item.get("url")),',
    )
    replace(
        "hourly_news_bot/src/models.py",
        "    parts = urlsplit(url.strip())",
        CANONICALIZE_GUARD,
    )
    replace(
        "hourly_news_bot/src/report.py",
        "from .models import NewsItem",
        "from .models import NewsItem, canonicalize_url",
    )
    replace(
        "hourly_news_bot/src/report.py",
        "escape(item.url, quote=True)",
        "escape(canonicalize_url(item.url), quote=True)",
        count=2,
    )


def fix_news_delivery_state() -> None:
    replace(
        "hourly_news_bot/src/main.py",
        '    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")',
        '    temporary = path.with_suffix(path.suffix + ".tmp")\n'
        '    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")\n'
        "    temporary.replace(path)",
    )
    replace(
        "hourly_news_bot/src/main.py",
        '    local_state_path = ROOT / "state" / "crawler-state.json"',
        "    if not gateway and not args.dry_run:\n"
        '        raise RuntimeError("Drive gateway is required for delivery; use --dry-run for local output")\n'
        '    local_state_path = ROOT / "state" / "crawler-state.json"',
    )
    replace(
        "hourly_news_bot/src/main.py",
        "    for item in new_items:\n"
        "        seen[item.key] = now.isoformat()\n"
        '    state = {"updated_at": now.isoformat(), "seen": prune_seen(seen, now)}\n'
        "    save_local_state(local_state_path, state)\n\n",
        "",
    )
    replace(
        "hourly_news_bot/src/main.py",
        "    elif not args.dry_run:\n"
        '        LOGGER.warning("Apps Script Drive gateway is not configured; files remain local")',
        "        # Delivery state may only advance after every upload succeeded.\n"
        "        # \u4e0a\u4f20\u5168\u90e8\u6210\u529f\u540e\u624d\u80fd\u63a8\u8fdb\u4ea4\u4ed8\u72b6\u6001\uff0c\u907f\u514d\u6f0f\u53d1\u3002\n"
        "        for item in new_items:\n"
        "            seen[item.key] = now.isoformat()\n"
        '        state = {"updated_at": now.isoformat(), "seen": prune_seen(seen, now)}\n'
        "        save_local_state(local_state_path, state)",
    )


def fix_russell_constituents() -> None:
    replace(
        "russell2000_market_report/universe.py",
        "from datetime import UTC, datetime, timedelta",
        "from datetime import UTC, date, datetime, timedelta",
    )
    replace(
        "russell2000_market_report/universe.py",
        '        as_of = ""\n    symbols = validate_universe',
        '        as_of = os.getenv("RUSSELL2000_CONSTITUENTS_AS_OF", "").strip()\n    symbols = validate_universe',
    )
    append_new(
        "russell2000_market_report/universe.py",
        "def validate_snapshot_as_of",
        SNAPSHOT_VALIDATOR,
    )
    replace(
        "russell2000_market_report/runtime.py",
        "from datetime import date",
        "from datetime import date, timedelta",
    )
    replace(
        "russell2000_market_report/runtime.py",
        "    normalize_symbol,",
        "    normalize_symbol,\n    validate_snapshot_as_of,",
    )
    replace(
        "russell2000_market_report/runtime.py",
        '        snapshot = load_russell2000_universe(project_root / ".cache" / "universe")',
        RUSSELL_GUARD,
    )
    replace(
        ".github/workflows/russell2000-daily-report.yml",
        '      top_n:\n        description: "Number of Russell 2000 gainers and losers"\n        required: true\n        default: "100"\n        type: string',
        WORKFLOW_INPUTS,
    )
    replace(
        ".github/workflows/russell2000-daily-report.yml",
        "          SEC_RPS: ${{ vars.SEC_RPS || '5' }}",
        "          SEC_RPS: ${{ vars.SEC_RPS || '5' }}\n"
        "          RUSSELL2000_CONSTITUENTS_FILE: ${{ github.event_name == 'workflow_dispatch' && inputs.historical_constituents_file || '' }}\n"
        "          RUSSELL2000_CONSTITUENTS_AS_OF: ${{ github.event_name == 'workflow_dispatch' && inputs.constituents_as_of || '' }}",
    )


def check_syntax() -> int:
    parts = sorted((ROOT / "market_report_src").glob("part_*.inc"))
    ast.parse("".join(path.read_text(encoding="utf-8") for path in parts), filename="market_report_src")
    checked = 0
    for file in sorted(ROOT.rglob("*.py")):
        relative = file.relative_to(ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        ast.parse(file.read_text(encoding="utf-8"), filename=str(relative))
        checked += 1
    return checked


def main() -> int:
    fix_template_escaping()
    fix_feed_defaults()
    fix_output_defaults()
    fix_sec_point_in_time()
    fix_external_links()
    fix_news_delivery_state()
    fix_russell_constituents()
    checked = check_syntax()
    print(f"Guarded review fixes applied; {checked} Python files and market_report_src parse cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
