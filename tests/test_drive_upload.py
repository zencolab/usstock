from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.upload_market_report_drive import build_archive, read_metadata


class MarketReportDriveUploadTests(unittest.TestCase):
    def test_build_archive_keeps_site_and_output_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            site = root / "site"
            output = root / "output"
            site.mkdir()
            (site / "assets").mkdir()
            output.mkdir()
            (output / "2026-08-21").mkdir()
            (site / "index.html").write_text("report", encoding="utf-8")
            (site / "assets" / "style.css").write_text("body{}", encoding="utf-8")
            (output / "2026-08-21" / "gainers.csv").write_text(
                "symbol,change\nABC,1.2\n", encoding="utf-8"
            )

            archive_path = build_archive(site, output, root / "report.zip")

            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    sorted(archive.namelist()),
                    [
                        "output/2026-08-21/gainers.csv",
                        "site/assets/style.css",
                        "site/index.html",
                    ],
                )

    def test_read_metadata_validates_report_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            site = Path(temporary_directory)
            (site / "metadata.json").write_text(
                json.dumps({"report_date": "2026-08-21", "mode": "live"}),
                encoding="utf-8",
            )
            metadata, report_date = read_metadata(site)
            self.assertEqual(report_date, "2026-08-21")
            self.assertEqual(metadata["mode"], "live")

    def test_read_metadata_rejects_invalid_report_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            site = Path(temporary_directory)
            (site / "metadata.json").write_text(
                json.dumps({"report_date": "not-a-date"}), encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                read_metadata(site)


if __name__ == "__main__":
    unittest.main()
