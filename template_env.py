"""Shared Jinja2 environment for every HTML report renderer.

The report templates are named ``*.html.j2``. Jinja2's
``select_autoescape(["html", "xml"])`` matches on the *final* filename suffix,
which for these templates is ``.j2``, so autoescaping silently stayed off and
external strings (SEC descriptions, news headlines, model output) were written
into the pages unescaped.

This module is the single place where the environment is built. Autoescaping is
turned on unconditionally instead of being inferred from a suffix, and a
``safe_external_url`` filter neutralises link targets that autoescaping cannot
protect (``javascript:`` and ``data:`` URLs in ``href``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader

# Only these schemes may appear in a rendered href/src attribute.
ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto"})

# Rendered in place of a rejected URL. "#" is inert in every browser.
BLOCKED_URL_PLACEHOLDER = "#"


def safe_external_url(value: Any) -> str:
    """Return ``value`` only when it is a safe absolute or relative URL.

    Autoescaping protects text content and attribute values, but it does not
    stop ``javascript:alert(1)`` from being a working ``href``. Data-source and
    model-provided URLs therefore go through this filter.
    """

    url = str(value or "").strip()
    if not url:
        return BLOCKED_URL_PLACEHOLDER
    # Control characters are used to smuggle "java\tscript:" past naive checks.
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        return BLOCKED_URL_PLACEHOLDER
    parts = urlsplit(url)
    if not parts.scheme:
        # Relative link inside the generated site. Reject protocol-relative
        # URLs ("//evil.example") because they inherit the page scheme.
        return BLOCKED_URL_PLACEHOLDER if url.startswith("//") else url
    if parts.scheme.lower() not in ALLOWED_URL_SCHEMES:
        return BLOCKED_URL_PLACEHOLDER
    return url


def build_environment(
    template_dir: Path | str,
    *,
    filters: Mapping[str, Callable[..., Any]] | None = None,
) -> Environment:
    """Build the shared, always-autoescaping Jinja2 environment."""

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["safe_external_url"] = safe_external_url
    if filters:
        env.filters.update(dict(filters))
    return env
