from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .crawler import NewsCrawler
from .drive_store import GoogleDriveStore
from .report import render_html, render_markdown
from .translator import OllamaTranslator

LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]


def load_sources(path: Path, selected: set[str] | None = None) -> list[dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = payload.get("sources", [])
    if selected:
        sources = [source for source in sources if source["id"] in selected]
    if not sources:
        raise ValueError("No sources selected")
    return sources


def load_local_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_local_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def prune_seen(seen: dict[str, str], now: datetime, days: int = 30) -> dict[str, str]:
    cutoff = now - timedelta(days=days)
    result: dict[str, str] = {}
    for key, timestamp in seen.items():
        try:
            value = datetime.fromisoformat(timestamp)
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            if value >= cutoff:
                result[key] = timestamp
        except (TypeError, ValueError):
            continue
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an hourly bilingual US-stock news digest")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "sources.yaml")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--source", action="append", help="Run only the specified source id")
    parser.add_argument("--dry-run", action="store_true", help="Write locally without Google Drive upload")
    parser.add_argument("--no-translate", action="store_true", help="Skip Ollama translation")
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

    now = datetime.now(UTC)
    timezone_name = os.getenv("REPORT_TIMEZONE") or "UTC"
    sources = load_sources(args.config, set(args.source or []) or None)

    service_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    drive = GoogleDriveStore(service_json, folder_id) if service_json and folder_id else None
    local_state_path = ROOT / "state" / "crawler-state.json"
    state = drive.load_json("crawler-state.json") if drive else load_local_state(local_state_path)
    state = state or {"seen": {}}
    seen = prune_seen(dict(state.get("seen", {})), now)

    crawler = NewsCrawler(
        user_agent=os.getenv(
            "CRAWLER_USER_AGENT",
            "BilingualMarketDigest/1.0 (+https://github.com/zencolab/usstock)",
        ),
        max_age_hours=float(os.getenv("MAX_AGE_HOURS") or "3"),
        max_items_per_source=int(os.getenv("MAX_ITEMS_PER_SOURCE") or "12"),
        request_delay_seconds=float(os.getenv("REQUEST_DELAY_SECONDS") or "1.5"),
    )
    try:
        fetched, source_counts, crawl_errors = crawler.fetch_all(sources)
    finally:
        crawler.close()

    new_items = [item for item in fetched if item.key not in seen]
    new_items.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)

    translation_errors: list[str] = []
    if new_items and not args.no_translate:
        translator = OllamaTranslator(
            base_url=os.getenv("OLLAMA_BASE_URL") or "https://ollama.com/api",
            api_key=os.getenv("OLLAMA_API_KEY", ""),
            model=os.getenv("OLLAMA_MODEL") or "gemma4:cloud",
        )
        try:
            translation_errors = translator.translate(new_items)
        finally:
            translator.close()

    all_errors = crawl_errors + [f"Translation: {error}" for error in translation_errors]
    markdown = render_markdown(
        new_items,
        generated_at=now,
        timezone_name=timezone_name,
        source_counts=source_counts,
        errors=all_errors,
    )
    html = render_html(
        new_items,
        generated_at=now,
        timezone_name=timezone_name,
        source_counts=source_counts,
        errors=all_errors,
    )
    report_payload = {
        "generated_at": now.isoformat(),
        "timezone": timezone_name,
        "source_counts": source_counts,
        "errors": all_errors,
        "items": [item.to_dict() for item in new_items],
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%MZ")
    markdown_name = f"us-stock-news-{stamp}.md"
    json_name = f"us-stock-news-{stamp}.json"
    html_name = f"us-stock-news-{stamp}.html"
    (args.output_dir / markdown_name).write_text(markdown, encoding="utf-8")
    (args.output_dir / html_name).write_text(html, encoding="utf-8")
    (args.output_dir / json_name).write_text(
        json.dumps(report_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for item in new_items:
        seen[item.key] = now.isoformat()
    state = {"updated_at": now.isoformat(), "seen": prune_seen(seen, now)}

    if args.dry_run or not drive:
        if not drive and not args.dry_run:
            LOGGER.warning("Drive is not configured; report was written locally only")
        save_local_state(local_state_path, state)
    else:
        drive.upload_text(markdown_name, markdown, "text/markdown")
        drive.upload_text("latest-us-stock-news.md", markdown, "text/markdown")
        drive.upload_text(html_name, html, "text/html")
        drive.upload_text("latest-us-stock-news.html", html, "text/html")
        drive.upload_text(
            json_name, json.dumps(report_payload, ensure_ascii=False, indent=2), "application/json"
        )
        drive.upload_text(
            "latest-us-stock-news.json",
            json.dumps(report_payload, ensure_ascii=False, indent=2),
            "application/json",
        )
        drive.save_json("crawler-state.json", state)

    LOGGER.info("Created %s with %s new items", markdown_name, len(new_items))
    return 0 if any(source_counts.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
