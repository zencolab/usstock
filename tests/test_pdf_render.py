from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.render_market_report_pdf import create_print_source, find_browser, validate_pdf


class MarketReportPdfTests(unittest.TestCase):
    def test_create_print_source_injects_base_style_and_note(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            site = root / "site"
            site.mkdir()
            (site / "index.html").write_text(
                "<!doctype html><html><head></head><body><main>Report</main></body></html>",
                encoding="utf-8",
            )
            destination = root / "print" / "index.html"
            create_print_source(site, destination)
            source = destination.read_text(encoding="utf-8")
            self.assertIn("<base href=", source)
            self.assertIn("@page { size: A4 landscape", source)
            self.assertIn("Drive 预览版", source)

    @patch("scripts.render_market_report_pdf.shutil.which")
    def test_find_browser_uses_available_chrome(self, mocked_which) -> None:
        mocked_which.side_effect = lambda name: "/usr/bin/google-chrome" if name == "google-chrome" else None
        self.assertEqual(find_browser(), "/usr/bin/google-chrome")

    def test_validate_pdf_accepts_pdf_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf = Path(temporary_directory) / "report.pdf"
            pdf.write_bytes(b"%PDF-1.7\n" + b"0" * 2048)
            validate_pdf(pdf)

    def test_validate_pdf_rejects_invalid_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            pdf = Path(temporary_directory) / "report.pdf"
            pdf.write_bytes(b"not a pdf")
            with self.assertRaises(RuntimeError):
                validate_pdf(pdf)


if __name__ == "__main__":
    unittest.main()
