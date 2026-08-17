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
from .drive_gateway import AppsScriptDriveGateway
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
    parser.add_argument("--dry-run", action="store_true", help="Write locally without gateway upload")
    parser.add_argument("--no-translate", action="store_true", help="Skip Ollama translation")
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT / ".env")
    args = parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

    now = datetime.now(UTC)
    timezone_name = os.getenv("REPORT_TIMEZONE") or "UTC"
    sources = load_sources(args.config, set(args.source or []) or None)

    gateway_url = os.getenv("DRIVE_GATEWAY_URL", "").strip()
    gateway_token = os.getenv("DRIVE_GATEWAY_TOKEN", "").strip()
    gateway = (
        AppsScriptDriveGateway(gateway_url, gateway_token)
        if gateway_url and gateway_token
        else None
    )

    local_state_path = ROOT / "state" / "crawler-state.json"
    state = load_local_state(local_state_path)
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
    json_text = json.dumps(report_payload, ensure_ascii=False, indent=2)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%MZ")
    run_id = now.strftime("%Y%m%d-%H%M%SZ")
    markdown_name = f"us-stock-news-{stamp}.md"
    json_name = f"us-stock-news-{stamp}.json"
    html_name = f"us-stock-news-{stamp}.html"
    (args.output_dir / markdown_name).write_text(markdown, encoding="utf-8")
    (args.output_dir / json_name).write_text(json_text, encoding="utf-8")
    (args.output_dir / html_name).write_text(html, encoding="utf-8")

    for item in new_items:
        seen[item.key] = now.isoformat()
    state = {"updated_at": now.isoformat(), "seen": prune_seen(seen, now)}
    save_local_state(local_state_path, state)

    if not args.dry_run and gateway:
        info = gateway.ping()
        target_path = info.get("us_stock_news_path")
        if not target_path:
            raise RuntimeError(
                "Drive gateway does not advertise us_stock_news_path; redeploy Apps Script"
            )
        LOGGER.info(
            "Connected to %s at %s",
            info.get("service", "Drive gateway"),
            target_path,
        )
        uploads = [
            (markdown_name, "text/markdown", markdown),
            (json_name, "application/json", json_text),
            (html_name, "text/html", html),
        ]
        for file_name, mime_type, content in uploads:
            result = gateway.upload_text(
                run_id=run_id,
                file_name=file_name,
                mime_type=mime_type,
                content=content,
            )
            LOGGER.info(
                "Drive %s: %s (%s)",
                result.get("status", "uploaded"),
                result.get("drive_path", file_name),
                result.get("web_view_link", ""),
            )
    elif not args.dry_run:
        LOGGER.warning("Apps Script Drive gateway is not configured; files remain local")

    LOGGER.info("Created %s, %s and %s with %s new items", markdown_name, json_name, html_name, len(new_items))
    return 0 if any(source_counts.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
