from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PRINT_CSS = r"""
@page { size: A4 portrait; margin: 12mm; }
:root {
  color-scheme: light !important;
  --canvas: #ffffff !important;
  --surface: #f7f7f6 !important;
  --surface-2: #efefed !important;
  --text: #252524 !important;
  --muted: #66635f !important;
  --border: #d9d7d3 !important;
  --accent: #2678c9 !important;
  --accent-soft: #e5f2fc !important;
  --warning: #9b4e19 !important;
  --warning-soft: #fbebde !important;
}
* {
  -webkit-print-color-adjust: exact !important;
  print-color-adjust: exact !important;
}
html, body { background: #ffffff !important; color: #252524 !important; }
body {
  font: 9.5pt/1.48 "Noto Sans CJK SC", "Noto Sans SC", Arial, sans-serif !important;
}
.page { width: 100% !important; margin: 0 !important; padding: 0 !important; }
.eyebrow { font-size: 8pt !important; }
h1 { font-size: 27pt !important; line-height: 1.08 !important; }
.subtitle { max-width: 170mm !important; font-size: 10.5pt !important; }
.generated { margin-top: 3mm !important; font-size: 8.5pt !important; }
.metrics { margin: 7mm 0 3mm !important; gap: 3mm !important; }
.metric { padding: 4mm !important; break-inside: avoid; }
.metric strong { font-size: 20pt !important; }
.source-status { margin: 3mm 0 8mm !important; gap: 2mm !important; }
.source-status li { padding: 2mm 3mm !important; }
.warnings { margin: 0 0 8mm !important; padding: 4mm !important; break-inside: avoid; }
.source-section { margin-top: 8mm !important; }
.section-heading { margin-bottom: 3mm !important; break-after: avoid; }
.section-heading h2 { font-size: 17pt !important; }
.news-grid { gap: 4mm !important; }
.news-card {
  padding: 5mm !important;
  border-radius: 2.5mm !important;
  background: #f7f7f6 !important;
  break-inside: avoid;
}
.news-card h3 { margin: 3mm 0 2mm !important; font-size: 13.5pt !important; }
.zh-title { font-size: 11.5pt !important; }
.summary { margin-top: 3mm !important; padding-top: 3mm !important; }
.summary p { margin: 2mm 0 !important; }
.source-link { min-height: 0 !important; margin-top: 2mm !important; }
a { color: inherit !important; text-decoration: none !important; }
.empty-state { margin-top: 8mm !important; padding: 12mm 5mm !important; }
footer { margin-top: 10mm !important; padding-top: 4mm !important; font-size: 8pt !important; }
"""


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
    raise RuntimeError("Chrome or Chromium is required to generate the bilingual-news PDF")


def prepare_print_document(document: str) -> str:
    if "</head>" not in document or "<body" not in document:
        raise ValueError("PDF source does not contain the expected document structure")
    document = document.replace("Hourly market monitor", "Two-hour market monitor", 1)
    return document.replace(
        "</head>",
        f'<style id="pdf-print-style">{PRINT_CSS}</style>\n</head>',
        1,
    )


def validate_pdf(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"PDF renderer did not create {path}")
    content = path.read_bytes()
    if len(content) < 1024 or not content.startswith(b"%PDF-"):
        raise RuntimeError(f"PDF renderer created an invalid or empty file: {path}")


def render_pdf(document: str, output: Path, browser: str = "") -> Path:
    executable = find_browser(browser)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    prepared = prepare_print_document(document)
    last_error = "unknown Chrome error"
    with tempfile.TemporaryDirectory(prefix="bilingual-news-pdf-") as temporary_directory:
        temporary = Path(temporary_directory)
        source = temporary / "digest.html"
        source.write_text(prepared, encoding="utf-8")

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
                "--virtual-time-budget=3000",
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
                return output
            except RuntimeError as exc:
                stderr = (completed.stderr or completed.stdout or "").strip()
                last_error = f"{exc}; exit={completed.returncode}; {stderr[-1200:]}"

    raise RuntimeError(f"Chrome failed to generate the bilingual-news PDF: {last_error}")
