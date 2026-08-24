from __future__ import annotations

import argparse
import json
import os
import re
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from hourly_news_bot.src.drive_gateway import AppsScriptDriveGateway

DEFAULT_GATEWAY_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbx3JuXMbJOAiiHhUUl3MIQrM2LYlZgCQhkiCNajewFpJxTiRl5cFMXjq45Z_gBqYvHB/exec"
)
DEFAULT_OPERATION = "us_stock_news_file"
PATH_KEYS = {
    "us_stock_news_file": "us_stock_news_path",
    "us_market_close_file": "us_market_close_path",
    "run_file": "base_path",
}


def read_metadata(site: Path) -> tuple[dict[str, Any], str]:
    metadata_path = site / "metadata.json"
    if not metadata_path.is_file():
        raise ValueError(f"Report metadata is missing: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("Report metadata must be a JSON object")
    report_date = str(metadata.get("report_date") or "").strip()
    try:
        date.fromisoformat(report_date)
    except ValueError as exc:
        raise ValueError("Report metadata contains an invalid report_date") from exc
    return metadata, report_date


def read_pdf(path: Path) -> bytes:
    if not path.is_file():
        raise ValueError(f"Drive preview PDF is missing: {path}")
    content = path.read_bytes()
    if len(content) < 1024 or not content.startswith(b"%PDF-"):
        raise ValueError(f"Drive preview PDF is invalid or empty: {path}")
    return content


def build_archive(site: Path, data_output: Path, destination: Path) -> Path:
    for directory in (site, data_output):
        if not directory.is_dir():
            raise ValueError(f"Report directory is missing: {directory}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for directory in (site, data_output):
            for path in sorted(item for item in directory.rglob("*") if item.is_file()):
                relative = Path(directory.name) / path.relative_to(directory)
                archive.write(path, relative.as_posix())

    if destination.stat().st_size == 0:
        raise ValueError("Generated Drive archive is empty")
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package a generated market-close report and upload it to Google Drive"
    )
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--data-output", type=Path, default=Path("output"))
    parser.add_argument("--archive-output", type=Path, default=Path(".drive-upload"))
    parser.add_argument("--pdf", type=Path)
    parser.add_argument(
        "--file-prefix",
        default=os.getenv("MARKET_REPORT_FILE_PREFIX") or "us-market-close",
    )
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("DRIVE_GATEWAY_URL") or DEFAULT_GATEWAY_URL,
    )
    parser.add_argument(
        "--gateway-token",
        default=os.getenv("DRIVE_GATEWAY_TOKEN") or "",
    )
    parser.add_argument(
        "--operation",
        default=os.getenv("DRIVE_MARKET_REPORT_OPERATION") or DEFAULT_OPERATION,
    )
    parser.add_argument("--run-id", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.gateway_token.strip():
        raise SystemExit("Missing AGENT_UPLOAD_TOKEN or DRIVE_GATEWAY_TOKEN")

    file_prefix = args.file_prefix.strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", file_prefix):
        raise SystemExit("file prefix may contain only letters, numbers, dots, underscores and hyphens")

    metadata, report_date = read_metadata(args.site)
    operation = args.operation.strip()
    run_id = args.run_id.strip() or datetime.now(UTC).strftime("%Y%m%d-%H%M%SZ")
    workflow_run_id = os.getenv("GITHUB_RUN_ID", "").strip()
    file_suffix = f"-{workflow_run_id}" if workflow_run_id else ""
    archive_name = f"{file_prefix}-{report_date}{file_suffix}.zip"
    metadata_name = f"{file_prefix}-{report_date}{file_suffix}-metadata.json"
    pdf_name = f"{file_prefix}-{report_date}{file_suffix}.pdf"
    archive_path = build_archive(
        args.site,
        args.data_output,
        args.archive_output / archive_name,
    )

    gateway = AppsScriptDriveGateway(args.gateway_url, args.gateway_token)
    info = gateway.ping()
    path_key = PATH_KEYS.get(operation)
    target_path = info.get(path_key) if path_key else None
    if path_key and not target_path:
        raise RuntimeError(
            f"Drive gateway does not advertise {path_key}; check the Apps Script deployment "
            f"or set DRIVE_MARKET_REPORT_OPERATION to a supported operation"
        )
    print(
        f"Connected to {info.get('service', 'Drive gateway')}"
        + (f" at {target_path}" if target_path else "")
    )

    uploads: list[tuple[str, str, bytes]] = []
    if args.pdf is not None:
        uploads.append((pdf_name, "application/pdf", read_pdf(args.pdf)))
    uploads.extend(
        [
            (archive_name, "application/zip", archive_path.read_bytes()),
            (
                metadata_name,
                "application/json",
                json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
            ),
        ]
    )
    for file_name, mime_type, content in uploads:
        result = gateway.upload_bytes(
            run_id=run_id,
            file_name=file_name,
            mime_type=mime_type,
            content=content,
            operation=operation,
        )
        print(
            f"Drive {result.get('status', 'uploaded')}: "
            f"{result.get('drive_path', file_name)} "
            f"{result.get('web_view_link', '')}".rstrip()
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
