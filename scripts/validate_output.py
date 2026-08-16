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
        "OLLAMA_API_KEY=",
        "OLLAMA_API_KEY_FALLBACK=",
    ]
    leaked = [token for token in forbidden_secrets if token in markup]
    if leaked:
        raise SystemExit(f"possible secret leak in generated HTML: {leaked}")

    if actual_mode == "live":
        stock_markup = "\n".join(
            (args.site / link).read_text(encoding="utf-8", errors="replace") for link in links
        )
        live_markup = markup + "\n" + stock_markup
        placeholders = [r"\bUP\d{3}\b", r"\bDN\d{3}\b", "演示公司", "deterministic demo fixtures"]
        found = [token for token in placeholders if re.search(token, live_markup)]
        if found:
            raise SystemExit(f"live report contains demo placeholders: {found}")
        sources = metadata.get("sources") or []
        if not any("Alpaca" in str(source) for source in sources):
            raise SystemExit("live metadata does not identify the Alpaca batch source")
        if not any("news" in str(source).lower() for source in sources):
            raise SystemExit("live metadata does not identify the news catalog source")
        if not any("translation" in str(source).lower() for source in sources):
            raise SystemExit("live metadata does not identify the translation source")
        if metadata.get("news_catalog_mode") != "all_titles":
            raise SystemExit("live metadata does not identify the all-title news catalog mode")
        if not metadata.get("news_window_start") or not metadata.get("news_window_end"):
            raise SystemExit("live metadata is missing the exact news date window")
        if "行业（中文）" not in live_markup or "行业（英文）" not in live_markup:
            raise SystemExit("stock pages do not contain bilingual industry labels")
        if "近三个月全部新闻标题" not in live_markup:
            raise SystemExit("stock pages do not contain the complete three-month news catalog")
        if "本地神经机器翻译" in live_markup:
            raise SystemExit("stock pages contain the obsolete local-neural translation wording")

        detail_files = sorted((args.site / "news").glob("*.html"))
        expected_details = int(metadata.get("news_detail_pages") or 0)
        if expected_details != len(detail_files):
            raise SystemExit(
                f"metadata says {expected_details} news detail pages, found {len(detail_files)}"
            )
        if not detail_files:
            raise SystemExit("live report did not generate any bilingual news detail pages")
        detail_markup = "\n".join(
            path.read_text(encoding="utf-8", errors="replace") for path in detail_files
        )
        if "英汉对照摘要" not in detail_markup or "打开英文原文" not in detail_markup:
            raise SystemExit("news detail pages are missing bilingual summaries or original links")
        translated_blocks = re.findall(r'class="news-translation"[^>]*>([^<]+)', detail_markup)
        if not translated_blocks:
            raise SystemExit("news detail pages did not render any translated summary paragraphs")
        if not any("翻译暂不可用" not in block for block in translated_blocks):
            raise SystemExit("all news translations are unavailable")

    print(
        f"validated report_date={metadata.get('report_date')} "
        f"mode={actual_mode} stock_pages={len(links)} "
        f"news_pages={metadata.get('news_detail_pages', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
