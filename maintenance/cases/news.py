from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src import main as app
from src.drive_gateway import DriveGatewayError
from src.models import NewsItem, canonicalize_url
from src.report import render_html


class DeliveryRegressionTests(unittest.TestCase):
    def setup_run(self, stack, root, *, dry_run=False, configured=True):
        item = NewsItem("test", "Test", "A fresh headline", "https://example.com/news", published_at=datetime.now(UTC))
        old = {"seen": {"previous": datetime.now(UTC).isoformat()}}
        state_file = root / "state/crawler-state.json"
        state_file.parent.mkdir(parents=True)
        state_file.write_text(json.dumps(old), encoding="utf-8")
        args = SimpleNamespace(config=root / "sources.yaml", output_dir=root / "output", source=None, dry_run=dry_run, no_translate=True)
        env = {"DRIVE_GATEWAY_URL": "https://example.com/gateway", "DRIVE_GATEWAY_TOKEN": "test-token"} if configured else {}
        stack.enter_context(patch.dict(os.environ, env, clear=True))
        stack.enter_context(patch.object(app, "ROOT", root))
        stack.enter_context(patch.object(app, "load_dotenv"))
        stack.enter_context(patch.object(app, "parse_args", return_value=args))
        stack.enter_context(patch.object(app, "load_sources", return_value=[{"id": "test", "name": "Test"}]))
        crawler = stack.enter_context(patch.object(app, "NewsCrawler")).return_value
        crawler.fetch_all.return_value = ([item], {"Test": 1}, [])
        gateway = stack.enter_context(patch.object(app, "AppsScriptDriveGateway")).return_value
        gateway.ping.return_value = {"us_stock_news_path": "test"}
        gateway.upload_bytes.return_value = {"status": "created"}
        def pdf(document, path):
            path.write_bytes(b"%PDF-1.7\n" + b"0" * 2048)
            return path
        stack.enter_context(patch.object(app, "render_pdf", side_effect=pdf))
        return item, old, state_file, gateway, crawler

    def test_failed_upload_does_not_advance_seen(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            item, old, path, gateway, _ = self.setup_run(stack, Path(folder))
            gateway.upload_bytes.side_effect = DriveGatewayError("simulated failure")
            with self.assertRaises(DriveGatewayError): app.main()
            self.assertEqual(json.loads(path.read_text()), old)
            self.assertNotIn(item.key, json.loads(path.read_text())["seen"])

    def test_partial_upload_does_not_advance_seen(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            _, old, path, gateway, _ = self.setup_run(stack, Path(folder))
            gateway.upload_bytes.side_effect = [{"status": "created"}, DriveGatewayError("second file failed")]
            with self.assertRaises(DriveGatewayError): app.main()
            self.assertEqual(json.loads(path.read_text()), old)

    def test_success_records_state_after_all_uploads(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            item, _, path, gateway, _ = self.setup_run(stack, Path(folder))
            def upload(**kwargs):
                self.assertNotIn(item.key, json.loads(path.read_text())["seen"])
                return {"status": "created"}
            gateway.upload_bytes.side_effect = upload
            self.assertEqual(app.main(), 0)
            self.assertEqual(gateway.upload_bytes.call_count, 3)
            self.assertIn(item.key, json.loads(path.read_text())["seen"])

    def test_dry_run_leaves_delivery_state_unchanged(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            _, old, path, gateway, _ = self.setup_run(stack, Path(folder), dry_run=True)
            self.assertEqual(app.main(), 0)
            gateway.upload_bytes.assert_not_called()
            self.assertEqual(json.loads(path.read_text()), old)

    def test_missing_gateway_fails_before_crawling(self):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            _, old, path, _, crawler = self.setup_run(stack, Path(folder), configured=False)
            with self.assertRaisesRegex(RuntimeError, "gateway"): app.main()
            crawler.fetch_all.assert_not_called()
            self.assertEqual(json.loads(path.read_text()), old)

    def test_state_replace_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            old = {"seen": {"old": "value"}}
            path.write_text(json.dumps(old))
            with patch.object(Path, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError): app.save_local_state(path, {"seen": {"new": "value"}})
            self.assertEqual(json.loads(path.read_text()), old)

    def test_url_normalizer_rejects_non_web_links(self):
        for value in ["javascript:alert(1)", "data:text/html,test", "//example.com/news", "https://user:password@example.com"]:
            with self.subTest(value=value): self.assertEqual(canonicalize_url(value), "")

    def test_html_renderer_rejects_unsafe_link(self):
        item = NewsItem("test", "Test", "Title", "javascript:alert(1)", title_zh="标题")
        text = render_html([item], generated_at=datetime.now(UTC), timezone_name="UTC", source_counts={"Test": 1}, errors=[])
        self.assertNotIn('href="javascript:', text)
        self.assertIn("标题", text)


if __name__ == "__main__": unittest.main()
