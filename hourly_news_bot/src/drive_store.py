from __future__ import annotations

import io
import json
from typing import Any


class GoogleDriveStore:
    def __init__(self, service_account_json: str, folder_id: str) -> None:
        if not service_account_json or not folder_id:
            raise ValueError("Google Drive credentials and folder ID are required")
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive"]
        )
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.folder_id = folder_id

    @staticmethod
    def _escape_query(value: str) -> str:
        return value.replace("\\", "\\\\").replace("'", "\\'")

    def _find(self, name: str) -> dict[str, Any] | None:
        escaped_name = self._escape_query(name)
        escaped_folder = self._escape_query(self.folder_id)
        response = (
            self.service.files()
            .list(
                q=f"name = '{escaped_name}' and '{escaped_folder}' in parents and trashed = false",
                fields="files(id,name,modifiedTime)",
                pageSize=10,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        files = response.get("files", [])
        return files[0] if files else None

    def upload_text(self, name: str, content: str, mime_type: str) -> str:
        from googleapiclient.http import MediaIoBaseUpload

        media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=False)
        existing = self._find(name)
        if existing:
            result = (
                self.service.files()
                .update(
                    fileId=existing["id"],
                    media_body=media,
                    fields="id,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        else:
            result = (
                self.service.files()
                .create(
                    body={"name": name, "parents": [self.folder_id]},
                    media_body=media,
                    fields="id,webViewLink",
                    supportsAllDrives=True,
                )
                .execute()
            )
        return str(result.get("webViewLink") or result["id"])

    def load_json(self, name: str) -> dict[str, Any] | None:
        from googleapiclient.http import MediaIoBaseDownload

        existing = self._find(name)
        if not existing:
            return None
        buffer = io.BytesIO()
        request = self.service.files().get_media(fileId=existing["id"], supportsAllDrives=True)
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return json.loads(buffer.getvalue().decode("utf-8"))

    def save_json(self, name: str, value: dict[str, Any]) -> str:
        return self.upload_text(
            name,
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            "application/json",
        )
