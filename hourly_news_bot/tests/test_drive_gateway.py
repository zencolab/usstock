from __future__ import annotations

import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from src.drive_gateway import AppsScriptDriveGateway, DriveGatewayError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class DriveGatewayTests(unittest.TestCase):
    @patch("src.drive_gateway.urlopen")
    def test_ping_uses_gateway_contract(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse(
            {"ok": True, "result": {"service": "CNINFO Drive Gateway"}}
        )
        gateway = AppsScriptDriveGateway("https://script.google.com/macros/s/test/exec", "secret")
        result = gateway.ping()
        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload, {"token": "secret", "operation": "ping"})
        self.assertEqual(result["service"], "CNINFO Drive Gateway")

    @patch("src.drive_gateway.urlopen")
    def test_us_stock_news_file_payload_matches_gateway(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse(
            {"ok": True, "result": {"status": "created", "drive_path": "x/report.html"}}
        )
        gateway = AppsScriptDriveGateway("https://script.google.com/macros/s/test/exec", "secret")
        content = "中英双语".encode("utf-8")
        result = gateway.upload_bytes(
            run_id="20260817-135300Z",
            file_name="report.html",
            mime_type="text/html",
            content=content,
        )
        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["operation"], "us_stock_news_file")
        self.assertEqual(payload["run_id"], "20260817-135300Z")
        self.assertEqual(payload["file_name"], "report.html")
        self.assertEqual(payload["mime_type"], "text/html")
        self.assertEqual(payload["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(base64.b64decode(payload["content_base64"]), content)
        self.assertEqual(result["status"], "created")

    @patch("src.drive_gateway.urlopen")
    def test_custom_operation_is_sent(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse(
            {"ok": True, "result": {"status": "created", "drive_path": "x/report.zip"}}
        )
        gateway = AppsScriptDriveGateway("https://script.google.com/macros/s/test/exec", "secret")
        gateway.upload_bytes(
            run_id="20260824-020000Z",
            file_name="report.zip",
            mime_type="application/zip",
            content=b"zip",
            operation="run_file",
        )
        request = mocked_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["operation"], "run_file")

    @patch("src.drive_gateway.urlopen")
    def test_gateway_error_is_raised(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse(
            {"ok": False, "code": "request_failed", "error": "上传令牌无效"}
        )
        gateway = AppsScriptDriveGateway(
            "https://script.google.com/macros/s/test/exec", "bad", max_attempts=1
        )
        with self.assertRaises(DriveGatewayError):
            gateway.ping()


if __name__ == "__main__":
    unittest.main()
