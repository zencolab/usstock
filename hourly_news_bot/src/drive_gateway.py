from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOGGER = logging.getLogger(__name__)


class DriveGatewayError(RuntimeError):
    pass


class AppsScriptDriveGateway:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        timeout_seconds: float = 90,
        max_attempts: int = 3,
    ) -> None:
        self.url = url.strip()
        self.token = token.strip()
        if not self.url:
            raise ValueError("DRIVE_GATEWAY_URL is required")
        if not self.token:
            raise ValueError("DRIVE_GATEWAY_TOKEN is required")
        if not self.url.startswith("https://"):
            raise ValueError("DRIVE_GATEWAY_URL must use HTTPS")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)

    def _post_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "BilingualMarketDigest/1.0",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            body = response.read().decode("utf-8")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise DriveGatewayError("Gateway returned a non-object response")
        if not result.get("ok"):
            code = result.get("code") or "request_failed"
            error = result.get("error") or "Unknown gateway error"
            raise DriveGatewayError(f"{code}: {error}")
        return result

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                return self._post_once(payload)
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, DriveGatewayError) as exc:
                last_error = exc
                if attempt + 1 < self.max_attempts:
                    LOGGER.warning("Drive gateway attempt %s failed: %s", attempt + 1, exc)
                    time.sleep(2**attempt)
        raise DriveGatewayError(f"Drive gateway failed after {self.max_attempts} attempts: {last_error}")

    def ping(self) -> dict[str, Any]:
        response = self._post({"token": self.token, "operation": "ping"})
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def upload_bytes(
        self,
        *,
        run_id: str,
        file_name: str,
        mime_type: str,
        content: bytes,
    ) -> dict[str, Any]:
        if not run_id.strip():
            raise ValueError("run_id is required")
        if not file_name or "/" in file_name or "\\" in file_name:
            raise ValueError("file_name must not contain path separators")
        payload = {
            "token": self.token,
            "operation": "run_file",
            "run_id": run_id,
            "sha256": hashlib.sha256(content).hexdigest(),
            "file_name": file_name,
            "mime_type": mime_type,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        response = self._post(payload)
        result = response.get("result")
        if not isinstance(result, dict):
            raise DriveGatewayError("Gateway response is missing result")
        return result

    def upload_text(
        self,
        *,
        run_id: str,
        file_name: str,
        mime_type: str,
        content: str,
    ) -> dict[str, Any]:
        return self.upload_bytes(
            run_id=run_id,
            file_name=file_name,
            mime_type=mime_type,
            content=content.encode("utf-8"),
        )
