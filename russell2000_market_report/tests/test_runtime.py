from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from russell2000_market_report.runtime import brand_output


class Russell2000RuntimeTests(unittest.TestCase):
    def test_brand_output_keeps_project_visibly_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            site = Path(temporary_directory)
            (site / "index.html").write_text(
                '<html><body><h1>美股收盘日报</h1><div class="meta-row"></div>'
                '<footer>来源：Massive</footer></body></html>',
                encoding="utf-8",
            )
            brand_output(site)
            content = (site / "index.html").read_text(encoding="utf-8")
            self.assertIn("罗素 2000 收盘日报", content)
            self.assertIn("股票池：罗素 2000", content)
            self.assertIn("iShares IWM", content)


if __name__ == "__main__":
    unittest.main()
