from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    parser.add_argument("--expected-min", type=int, required=True)
    args = parser.parse_args()

    index = args.site / "index.html"
    metadata_file = args.site / "metadata.json"
    if not index.is_file() or not metadata_file.is_file():
        raise SystemExit("index.html or metadata.json is missing")

    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    markup = index.read_text(encoding="utf-8")
    links = sorted(set(re.findall(r'href="(stocks/[^"]+\.html)"', markup)))
    expected = args.expected_min * 2
    if len(links) < expected:
        raise SystemExit(f"expected at least {expected} stock pages, found {len(links)}")

    missing = [link for link in links if not (args.site / link).is_file()]
    if missing:
        raise SystemExit(f"missing linked stock pages: {missing[:5]}")

    forbidden = ["MASSIVE_API_KEY=", "SEC_USER_AGENT="]
    leaked = [token for token in forbidden if token in markup]
    if leaked:
        raise SystemExit(f"possible secret leak in generated HTML: {leaked}")

    print(
        f"validated report_date={metadata.get('report_date')} "
        f"mode={metadata.get('mode')} stock_pages={len(links)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
