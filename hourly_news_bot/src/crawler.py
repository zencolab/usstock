from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html import unescape
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .models import NewsItem, canonicalize_url

LOGGER = logging.getLogger(__name__)


class FetchError(RuntimeError):
    pass


@dataclass(slots=True)
class WebResponse:
    content: bytes
    charset: str = "utf-8"

    @property
    def text(self) -> str:
        return self.content.decode(self.charset, errors="replace")


def clean_text(value: str | None, limit: int = 900) -> str:
    if not value:
        return ""
    text = BeautifulSoup(unescape(value), "lxml").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit].rstrip()


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        return None


def _iter_json_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_json_nodes(child)


def extract_jsonld_articles(html: str, base_url: str, source: dict[str, Any]) -> list[NewsItem]:
    soup = BeautifulSoup(html, "lxml")
    results: list[NewsItem] = []
    article_types = {"Article", "NewsArticle", "ReportageNewsArticle"}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text(strip=True))
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _iter_json_nodes(payload):
            node_type = node.get("@type")
            types = set(node_type if isinstance(node_type, list) else [node_type])
            if not (types & article_types):
                continue
            title = clean_text(node.get("headline") or node.get("name"), 300)
            url_value = node.get("url") or node.get("mainEntityOfPage")
            if isinstance(url_value, dict):
                url_value = url_value.get("@id") or url_value.get("url")
            link = canonicalize_url(urljoin(base_url, str(url_value or "")))
            if not title or not link:
                continue
            results.append(
                NewsItem(
                    source_id=source["id"],
                    source_name=source["name"],
                    title_en=title,
                    url=link,
                    summary_en=clean_text(node.get("description")),
                    published_at=parse_datetime(node.get("datePublished")),
                )
            )
    return results


class NewsCrawler:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 25,
        request_delay_seconds: float = 1.5,
        max_age_hours: float = 3,
        max_items_per_source: int = 12,
    ) -> None:
        self.user_agent = user_agent
        self.request_delay_seconds = request_delay_seconds
        self.max_age_hours = max_age_hours
        self.max_items_per_source = max_items_per_source
        self.timeout_seconds = timeout_seconds
        self.headers = {"User-Agent": user_agent, "Accept-Language": "en-US,en;q=0.9"}
        self._robots: dict[str, RobotFileParser | None] = {}

    def close(self) -> None:
        return None

    def _request(self, url: str) -> WebResponse:
        request = Request(url, headers=self.headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                content = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
                return WebResponse(content=content, charset=charset)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise FetchError(f"Request failed for {url}: {exc}") from exc

    def _robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            robots_url = f"{origin}/robots.txt"
            try:
                response = self._request(robots_url)
                robot = RobotFileParser()
                robot.set_url(robots_url)
                robot.parse(response.text.splitlines())
                self._robots[origin] = robot
            except FetchError:
                self._robots[origin] = None
        robot = self._robots[origin]
        return True if robot is None else robot.can_fetch(self.user_agent, url)

    def _get(self, url: str) -> WebResponse:
        if not self._robots_allowed(url):
            raise PermissionError(f"robots.txt does not allow crawling: {url}")
        return self._request(url)

    def _from_feed(self, source: dict[str, Any], feed_url: str) -> list[NewsItem]:
        response = self._get(feed_url)
        root = ET.fromstring(response.content)
        items: list[NewsItem] = []

        def local_name(tag: str) -> str:
            return tag.rsplit("}", 1)[-1].lower()

        def child_value(entry: ET.Element, *names: str) -> str:
            wanted = {name.lower() for name in names}
            for child in entry:
                if local_name(child.tag) not in wanted:
                    continue
                if local_name(child.tag) == "link" and child.attrib.get("href"):
                    return child.attrib["href"]
                return "".join(child.itertext()).strip()
            return ""

        entries = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
        for entry in entries:
            title = clean_text(child_value(entry, "title"), 300)
            link = canonicalize_url(urljoin(feed_url, child_value(entry, "link")))
            if not title or not link:
                continue
            published = parse_datetime(child_value(entry, "pubDate", "published", "updated", "created"))
            items.append(
                NewsItem(
                    source_id=source["id"],
                    source_name=source["name"],
                    title_en=title,
                    url=link,
                    summary_en=clean_text(child_value(entry, "summary", "description", "content")),
                    published_at=published,
                )
            )
        return items

    @staticmethod
    def _same_site(article_url: str, home_url: str) -> bool:
        article_host = (urlparse(article_url).hostname or "").removeprefix("www.")
        home_host = (urlparse(home_url).hostname or "").removeprefix("www.")
        return article_host == home_host or article_host.endswith(f".{home_host}")

    @staticmethod
    def _looks_like_article(url: str, title: str) -> bool:
        if len(title) < 20:
            return False
        path = urlparse(url).path.lower()
        blocked = ("/video/", "/live/", "/watch/", "/podcast/")
        if any(token in path for token in blocked):
            return False
        signals = ("/news/", "/article", "/articles/", "/markets/", "/finance/")
        return any(token in path for token in signals) or len(path.strip("/").split("/")) >= 2

    def _from_html(self, source: dict[str, Any]) -> list[NewsItem]:
        home_url = source["home_url"]
        response = self._get(home_url)
        html = response.text
        soup = BeautifulSoup(html, "lxml")
        items = extract_jsonld_articles(html, home_url, source)

        for selector in source.get("selectors", []):
            for container in soup.select(selector["container"]):
                link_node = container.select_one(selector.get("link", "a[href]"))
                if link_node is None or not link_node.get("href"):
                    continue
                title_node = container.select_one(selector.get("title", "h1,h2,h3"))
                title = clean_text(
                    title_node.get_text(" ", strip=True) if title_node else link_node.get_text(" ", strip=True),
                    300,
                )
                link = canonicalize_url(urljoin(home_url, link_node.get("href", "")))
                if not self._same_site(link, home_url) or not self._looks_like_article(link, title):
                    continue
                summary_node = container.select_one(selector.get("summary", "p"))
                time_node = container.select_one(selector.get("time", "time"))
                published_value = None
                if time_node:
                    published_value = time_node.get("datetime") or time_node.get_text(" ", strip=True)
                items.append(
                    NewsItem(
                        source_id=source["id"],
                        source_name=source["name"],
                        title_en=title,
                        url=link,
                        summary_en=clean_text(
                            summary_node.get_text(" ", strip=True) if summary_node else ""
                        ),
                        published_at=parse_datetime(published_value),
                    )
                )

        if not items:
            for link_node in soup.select("a[href]"):
                title = clean_text(link_node.get_text(" ", strip=True), 300)
                link = canonicalize_url(urljoin(home_url, link_node.get("href", "")))
                if self._same_site(link, home_url) and self._looks_like_article(link, title):
                    items.append(
                        NewsItem(
                            source_id=source["id"],
                            source_name=source["name"],
                            title_en=title,
                            url=link,
                        )
                    )
        return items

    def fetch_source(self, source: dict[str, Any]) -> list[NewsItem]:
        errors: list[str] = []
        items: list[NewsItem] = []
        for feed_url in source.get("feeds", []):
            try:
                items.extend(self._from_feed(source, feed_url))
                if items:
                    break
            except (FetchError, PermissionError, ValueError, ET.ParseError) as exc:
                errors.append(f"{feed_url}: {exc}")
        if not items:
            try:
                items.extend(self._from_html(source))
            except (FetchError, PermissionError, ValueError) as exc:
                errors.append(f"{source['home_url']}: {exc}")
        if not items and errors:
            raise RuntimeError("; ".join(errors))

        cutoff = datetime.now(UTC) - timedelta(hours=self.max_age_hours)
        deduped: dict[str, NewsItem] = {}
        for item in items:
            if item.published_at and item.published_at < cutoff:
                continue
            deduped.setdefault(item.key, item)
        result = sorted(
            deduped.values(),
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        limit = int(source.get("max_items_per_source", self.max_items_per_source))
        return result[:limit]

    def fetch_all(
        self, sources: list[dict[str, Any]]
    ) -> tuple[list[NewsItem], dict[str, int], list[str]]:
        all_items: list[NewsItem] = []
        counts: dict[str, int] = {}
        errors: list[str] = []
        for index, source in enumerate(sources):
            if index:
                time.sleep(self.request_delay_seconds)
            try:
                items = self.fetch_source(source)
                all_items.extend(items)
                counts[source["name"]] = len(items)
                LOGGER.info("Fetched %s items from %s", len(items), source["name"])
            except Exception as exc:  # isolate source failures
                counts[source["name"]] = 0
                errors.append(f"{source['name']}: {exc}")
                LOGGER.exception("Source failed: %s", source["name"])
        unique = {item.key: item for item in all_items}
        return list(unique.values()), counts, errors
