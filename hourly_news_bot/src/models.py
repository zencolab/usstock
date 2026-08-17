from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "guccounter",
    "guce_referrer",
    "cmpid",
    "mod",
}


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), "")
    )


@dataclass(slots=True)
class NewsItem:
    source_id: str
    source_name: str
    title_en: str
    url: str
    summary_en: str = ""
    published_at: datetime | None = None
    title_zh: str = ""
    summary_zh: str = ""

    @property
    def key(self) -> str:
        material = f"{canonicalize_url(self.url)}\n{self.title_en.strip().lower()}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        data["key"] = self.key
        return data
