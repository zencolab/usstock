#!/usr/bin/env python3
"""Fail if a generated site contains link targets with unsafe schemes.

This is the publish-time guard for review issue #1. Autoescaping is asserted by
the unit tests; this script checks the *rendered* output so a future template
or renderer change cannot silently reintroduce the problem.

    python scripts/assert_escaped_output.py site
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# href/src values whose scheme can execute script or smuggle a document.
UNSAFE_ATTRIBUTE = re.compile(
    r"""(?:href|src)\s*=\s*["']\s*(?:javascript|vbscript|data)\s*:""",
    re.IGNORECASE,
)
# A raw inline event handler in generated markup.
INLINE_EVENT_HANDLER = re.compile(r"<[^>]+\son(?:error|load|click)\s*=", re.IGNORECASE)


def scan(site: Path) -> list[str]:
    problems: list[str] = []
    files = sorted(site.rglob("*.html"))
    if not files:
        problems.append(f"No HTML files found under {site}")
        return problems
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in UNSAFE_ATTRIBUTE.finditer(text):
            problems.append(f"{path}: unsafe link scheme -> {match.group(0)!r}")
        for match in INLINE_EVENT_HANDLER.finditer(text):
            problems.append(f"{path}: inline event handler -> {match.group(0)!r}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path, help="Generated site directory")
    args = parser.parse_args()

    if not args.site.exists():
        print(f"error: {args.site} does not exist", file=sys.stderr)
        return 2

    problems = scan(args.site)
    if problems:
        print(f"Found {len(problems)} unsafe pattern(s):", file=sys.stderr)
        for problem in problems[:50]:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"OK: no unsafe link schemes or inline handlers under {args.site}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
