from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from html import escape
from pathlib import Path

PRINT_CSS = r"""
@page { size: A4 landscape; margin: 10mm; }
:root {
  color-scheme: light !important;
  --canvas: #ffffff !important;
  --surface: #f9f8f7 !important;
  --surface-2: #f0efed !important;
  --text: #2c2c2b !important;
  --muted: #67645f !important;
  --border: #d9d7d3 !important;
  --accent: #2783de !important;
  --accent-soft: #e5f2fc !important;
  --positive: #2f8a5b !important;
  --negative: #d94f45 !important;
  --warning: #b96c2f !important;
  --shadow: none !important;
}
* { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }
html { scroll-behavior: auto !important; }
body {
  background: #ffffff !important;
  color: #2c2c2b !important;
  font: 9pt/1.38 "Noto Sans CJK SC", "Noto Sans SC", Arial, sans-serif !important;
}
.container { width: 100% !important; max-width: none !important; margin: 0 !important; }
.pdf-summary-note {
  margin: 0 0 6mm;
  padding: 3mm 4mm;
  border: 1px solid #cfe2f5;
  border-radius: 2mm;
  background: #eef6fd;
  color: #315b7d;
  font-size: 9pt;
}
.hero { padding: 0 0 7mm !important; }
.eyebrow { font-size: 8pt !important; }
h1 { margin: 2mm 0 !important; font-size: 24pt !important; }
h2 { margin-bottom: 4mm !important; font-size: 16pt !important; }
h3 { font-size: 11pt !important; }
.subhead { max-width: 190mm !important; font-size: 10pt !important; }
.meta-row { margin-top: 4mm !important; }
.source-badge { min-height: 0 !important; padding: 1.5mm 2.2mm !important; font-size: 8pt !important; }
.section { padding: 7mm 0 !important; }
.grid-3 { gap: 4mm !important; }
.card { padding: 4mm !important; border-radius: 2.5mm !important; box-shadow: none !important; break-inside: avoid; }
.index-card .price { font-size: 16pt !important; }
.chart { padding-top: 3mm !important; }
.chart svg { max-height: 42mm; }
.toolbar { display: none !important; }
.ranking { break-before: page; }
.table-wrap { overflow: visible !important; border-radius: 2mm !important; }
table { width: 100% !important; min-width: 0 !important; table-layout: fixed; font-size: 9pt; }
thead { display: table-header-group; }
tr { break-inside: avoid; }
th, td { padding: 2.2mm 1.7mm !important; overflow-wrap: anywhere; }
th { font-size: 7.5pt !important; }
th:nth-child(6), td:nth-child(6),
th:nth-child(8), td:nth-child(8),
th:nth-child(9), td:nth-child(9) { display: none; }
th:nth-child(1), td:nth-child(1) { width: 7%; }
th:nth-child(2), td:nth-child(2) { width: 9%; }
th:nth-child(3), td:nth-child(3) { width: 22%; }
th:nth-child(4), td:nth-child(4) { width: 10%; }
th:nth-child(5), td:nth-child(5) { width: 10%; }
th:nth-child(7), td:nth-child(7) { width: 13%; }
th:nth-child(10), td:nth-child(10) { width: 29%; }
.company, .concepts { max-width: none !important; }
a { color: inherit !important; text-decoration: none !important; }
.notice { padding: 3mm 4mm !important; }
.footer { padding: 7mm 0 0 !important; font-size: 8pt !important; }
"""

PDF_NOTE = (
    '<aside class="pdf-summary-note">'
    "Drive 预览版包含三大指数与涨跌幅榜摘要；完整个股、双语新闻详情及原始数据保存在 GitHub Pages 和 ZIP 归档中。"
    "</aside>"
)


def find_browser(explicit: str = "") -> str:
    requested = explicit.strip() or os.getenv("CHROME_BIN", "").strip()
    if requested:
        resolved = shutil.which(requested)
        if resolved:
            return resolved
        candidate = Path(requested).expanduser()
        if candidate.is_file():
            return str(candidate.resolve())
        raise RuntimeError(f"Requested Chrome/Chromium executable was not found: {requested}")

    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise RuntimeError("Chrome or Chromium is required to render the Drive preview PDF")


def create_print_source(site: Path, destination: Path) -> Path:
    index = site / "index.html"
    if not index.is_file():
        raise ValueError(f"Report index is missing: {index}")
    source = index.read_text(encoding="utf-8")
    if "</head>" not in source or "<body>" not in source:
        raise ValueError("Report index does not contain the expected HTML structure")

    base_href = site.resolve().as_uri().rstrip("/") + "/"
    additions = (
        f'<base href="{escape(base_href, quote=True)}">\n'
        f"<style>{PRINT_CSS}</style>\n"
    )
    source = source.replace("</head>", additions + "</head>", 1)
    source = source.replace("<body>", "<body>\n" + PDF_NOTE, 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")
    return destination


def validate_pdf(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"PDF renderer did not create {path}")
    content = path.read_bytes()
    if len(content) < 1024 or not content.startswith(b"%PDF-"):
        raise RuntimeError(f"PDF renderer created an invalid or empty file: {path}")


def render_pdf(site: Path, output: Path, browser: str = "") -> Path:
    executable = find_browser(browser)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    last_error = "unknown Chrome error"
    with tempfile.TemporaryDirectory(prefix="market-report-pdf-") as temporary_directory:
        temporary = Path(temporary_directory)
        source = create_print_source(site, temporary / "index.html")
        for headless_flag in ("--headless=new", "--headless"):
            if output.exists():
                output.unlink()
            profile = temporary / headless_flag.removeprefix("--").replace("=", "-")
            command = [
                executable,
                headless_flag,
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--hide-scrollbars",
                "--allow-file-access-from-files",
                "--force-color-profile=srgb",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=5000",
                "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={output}",
                source.resolve().as_uri(),
            ]
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            try:
                validate_pdf(output)
                print(f"Rendered Drive preview PDF: {output} ({output.stat().st_size} bytes)")
                return output
            except RuntimeError as exc:
                stderr = (completed.stderr or completed.stdout or "").strip()
                last_error = f"{exc}; exit={completed.returncode}; {stderr[-1200:]}"

    raise RuntimeError(f"Chrome failed to render the Drive preview PDF: {last_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render the market-close index as a Drive-previewable PDF")
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--browser", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    render_pdf(args.site, args.output, args.browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
