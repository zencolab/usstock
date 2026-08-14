from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    parser.add_argument("--expected-min", type=int, required=True)
    parser.add_argument("--expected-mode", choices=["live", "demo"])
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

    actual_mode = metadata.get("mode")
    if args.expected_mode and actual_mode != args.expected_mode:
        raise SystemExit(f"expected mode={args.expected_mode}, found mode={actual_mode}")

    forbidden_secrets = [
        "MASSIVE_API_KEY=",
        "ALPACA_API_KEY_ID=",
        "ALPACA_API_SECRET_KEY=",
        "SEC_USER_AGENT=",
    ]
    leaked = [token for token in forbidden_secrets if token in markup]
    if leaked:
        raise SystemExit(f"possible secret leak in generated HTML: {leaked}")

    if actual_mode == "live":
        live_markup = markup + "\n" + "\n".join(
            (args.site / link).read_text(encoding="utf-8", errors="replace") for link in links
        )
        placeholders = [r"\bUP\d{3}\b", r"\bDN\d{3}\b", "演示公司", "deterministic demo fixtures"]
        found = [token for token in placeholders if re.search(token, live_markup)]
        if found:
            raise SystemExit(f"live report contains demo placeholders: {found}")
        sources = metadata.get("sources") or []
        if not any("Alpaca" in str(source) for source in sources):
            raise SystemExit("live metadata does not identify the Alpaca batch source")

    print(
        f"validated report_date={metadata.get('report_date')} "
        f"mode={actual_mode} stock_pages={len(links)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
