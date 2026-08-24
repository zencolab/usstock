from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("site", type=Path)
    parser.add_argument("--expected-mode", choices=["live", "demo"], required=True)
    parser.add_argument("--expected-top-n", type=int, required=True)
    args = parser.parse_args()

    metadata_path = args.site / "metadata.json"
    index_path = args.site / "index.html"
    if not metadata_path.is_file() or not index_path.is_file():
        raise SystemExit("Russell 2000 metadata.json or index.html is missing")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    index = index_path.read_text(encoding="utf-8")

    if metadata.get("mode") != args.expected_mode:
        raise SystemExit(f"expected mode={args.expected_mode}, found {metadata.get('mode')}")
    if metadata.get("universe_name") != "Russell 2000":
        raise SystemExit("metadata does not identify the Russell 2000 universe")
    if metadata.get("top_n") != args.expected_top_n:
        raise SystemExit(
            f"expected top_n={args.expected_top_n}, found {metadata.get('top_n')}"
        )
    if "罗素 2000 收盘日报" not in index or "股票池：罗素 2000" not in index:
        raise SystemExit("index page is missing Russell 2000 branding")

    if args.expected_mode == "live":
        constituent_count = int(metadata.get("universe_constituents") or 0)
        if not 1500 <= constituent_count <= 2500:
            raise SystemExit(f"invalid live universe size: {constituent_count}")
        matched = metadata.get("universe_matched_by_date") or {}
        if len(matched) < 2 or min(int(value) for value in matched.values()) < 1000:
            raise SystemExit(f"insufficient Massive matches for the Russell 2000 universe: {matched}")
        if "IWM" not in " ".join(str(value) for value in metadata.get("sources") or []):
            raise SystemExit("live metadata does not identify the iShares IWM universe source")

    print(
        f"validated Russell 2000 report_date={metadata.get('report_date')} "
        f"mode={metadata.get('mode')} top_n={metadata.get('top_n')} "
        f"constituents={metadata.get('universe_constituents', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
