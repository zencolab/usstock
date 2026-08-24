from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.pdf_renderer import prepare_print_document, render_pdf, validate_pdf


class PdfRendererTests(unittest.TestCase):
    def test_prepare_print_document_adds_a4_style_and_two_hour_label(self) -> None:
        source = "<!doctype html><html><head></head><body>Hourly market monitor</body></html>"
        result = prepare_print_document(source)
        self.assertIn("@page { size: A4 portrait", result)
        self.assertIn("Two-hour market monitor", result)
        self.assertNotIn(">Hourly market monitor<", result)

    def test_validate_pdf_rejects_invalid_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "bad.pdf"
            path.write_bytes(b"not a pdf")
            with self.assertRaises(RuntimeError):
                validate_pdf(path)

    def test_render_pdf_keeps_only_the_pdf_output(self) -> None:
        source = "<!doctype html><html><head></head><body>Digest</body></html>"
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_directory = Path(temporary_directory) / "output"
            output = output_directory / "digest.pdf"

            def fake_run(command, **kwargs):
                output_argument = next(
                    argument for argument in command if argument.startswith("--print-to-pdf=")
                )
                rendered_path = Path(output_argument.split("=", 1)[1])
                rendered_path.write_bytes(b"%PDF-1.7\n" + b"0" * 2048)
                return SimpleNamespace(returncode=0, stderr="", stdout="")

            with patch("src.pdf_renderer.find_browser", return_value="/usr/bin/chrome"), patch(
                "src.pdf_renderer.subprocess.run", side_effect=fake_run
            ):
                result = render_pdf(source, output)

            self.assertEqual(result, output.resolve())
            validate_pdf(result)
            self.assertEqual(list(output_directory.glob("*.html")), [])


if __name__ == "__main__":
    unittest.main()
