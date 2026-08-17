from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .models import NewsItem


def render_markdown(
    items: list[NewsItem],
    *,
    generated_at: datetime,
    timezone_name: str,
    source_counts: dict[str, int],
    errors: list[str],
) -> str:
    local_time = generated_at.astimezone(ZoneInfo(timezone_name))
    lines = [
        "# US Stock News Digest / 美股资讯双语快报",
        "",
        f"Generated / 生成时间：{local_time:%Y-%m-%d %H:%M:%S %Z}",
        "",
        "> Public headlines and short summaries only. No paywall bypass or full-article reproduction.",
        "> 仅收集公开标题和短摘要，不绕过付费墙，也不复制全文。",
        "",
        "## Source status / 来源状态",
        "",
    ]
    for source, count in source_counts.items():
        lines.append(f"- {source}: {count}")

    if errors:
        lines.extend(["", "## Warnings / 警告", ""])
        lines.extend(f"- {error}" for error in errors)

    if not items:
        lines.extend(["", "## No new items / 暂无新增资讯", ""])
        return "\n".join(lines).rstrip() + "\n"

    groups: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        groups[item.source_name].append(item)

    for source_name, group in groups.items():
        lines.extend(["", f"## {source_name}", ""])
        for item in group:
            lines.append(f"### [{item.title_en}]({item.url})")
            lines.append("")
            lines.append(f"**中文：{item.title_zh or '（翻译暂不可用）'}**")
            lines.append("")
            if item.summary_en:
                lines.append(f"- **EN:** {item.summary_en}")
                lines.append(f"- **中文：** {item.summary_zh or '（翻译暂不可用）'}")
            if item.published_at:
                published = item.published_at.astimezone(ZoneInfo(timezone_name))
                lines.append(f"- **Published / 发布时间：** {published:%Y-%m-%d %H:%M %Z}")
            lines.append(f"- **Source / 原文：** {item.url}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_html(
    items: list[NewsItem],
    *,
    generated_at: datetime,
    timezone_name: str,
    source_counts: dict[str, int],
    errors: list[str],
) -> str:
    zone = ZoneInfo(timezone_name)
    local_time = generated_at.astimezone(zone)
    total_items = len(items)
    active_sources = sum(1 for count in source_counts.values() if count > 0)

    status_cards = "".join(
        f'<li><span>{escape(source)}</span><strong>{count}</strong></li>'
        for source, count in source_counts.items()
    )
    warning_block = ""
    if errors:
        warning_items = "".join(f"<li>{escape(error)}</li>" for error in errors)
        warning_block = f"""
        <section class="warnings" aria-labelledby="warnings-title">
          <h2 id="warnings-title">Warnings <span lang="zh-CN">/ 警告</span></h2>
          <ul>{warning_items}</ul>
        </section>"""

    grouped: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        grouped[item.source_name].append(item)

    source_sections: list[str] = []
    for source_index, (source_name, group) in enumerate(grouped.items(), start=1):
        source_id = f"source-{source_index}"
        cards: list[str] = []
        for item in group:
            summary = ""
            if item.summary_en:
                summary = f"""
                <div class="summary">
                  <p lang="en"><span class="label">EN</span>{escape(item.summary_en)}</p>
                  <p lang="zh-CN"><span class="label">中文</span>{escape(item.summary_zh or '翻译暂不可用')}</p>
                </div>"""
            published = ""
            if item.published_at:
                published_time = item.published_at.astimezone(zone)
                published = (
                    '<time datetime="'
                    + escape(item.published_at.isoformat(), quote=True)
                    + '">'
                    + escape(published_time.strftime("%Y-%m-%d %H:%M %Z"))
                    + "</time>"
                )
            cards.append(
                f"""
                <article class="news-card">
                  <div class="news-meta">
                    <span>{escape(source_name)}</span>
                    {published}
                  </div>
                  <h3 lang="en"><a href="{escape(item.url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(item.title_en)}</a></h3>
                  <p class="zh-title" lang="zh-CN">{escape(item.title_zh or '翻译暂不可用')}</p>
                  {summary}
                  <a class="source-link" href="{escape(item.url, quote=True)}" target="_blank" rel="noopener noreferrer">Read original <span lang="zh-CN">/ 查看原文</span><span aria-hidden="true"> ↗</span></a>
                </article>"""
            )
        source_sections.append(
            f"""
            <section class="source-section" aria-labelledby="{source_id}">
              <div class="section-heading">
                <h2 id="{source_id}">{escape(source_name)}</h2>
                <span>{len(group)} items</span>
              </div>
              <div class="news-grid">{''.join(cards)}</div>
            </section>"""
        )

    content = "".join(source_sections)
    if not items:
        content = """
        <section class="empty-state">
          <p class="empty-mark" aria-hidden="true">—</p>
          <h2>No new items</h2>
          <p lang="zh-CN">本次运行没有发现新增资讯。</p>
        </section>"""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>US Stock News Digest / 美股资讯双语快报</title>
  <style>
    :root {{
      color-scheme: light dark;
      --canvas: #ffffff;
      --surface: #f9f8f7;
      --surface-2: #f0efed;
      --text: #2c2c2b;
      --muted: #6e6b66;
      --border: #e6e5e3;
      --accent: #2783de;
      --accent-soft: #e5f2fc;
      --warning: #a9571e;
      --warning-soft: #fbebde;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--canvas);
      color: var(--text);
      font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", Arial, sans-serif;
    }}
    a {{ color: inherit; }}
    a:focus-visible {{ outline: 3px solid var(--accent); outline-offset: 3px; border-radius: 4px; }}
    .page {{ width: min(100% - 48px, 1080px); margin: 0 auto; padding: 56px 0 72px; }}
    .eyebrow {{ margin: 0 0 12px; color: var(--accent); font-size: 14px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }}
    h1 {{ max-width: 780px; margin: 0; font-size: clamp(36px, 6vw, 64px); line-height: 1.05; letter-spacing: -.035em; }}
    .subtitle {{ max-width: 680px; margin: 20px 0 0; color: var(--muted); font-size: 18px; }}
    .generated {{ display: inline-block; margin-top: 20px; color: var(--muted); font-size: 14px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 40px 0 16px; }}
    .metric {{ padding: 20px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }}
    .metric strong {{ display: block; font-size: 30px; line-height: 1.1; }}
    .metric span {{ color: var(--muted); font-size: 14px; }}
    .source-status {{ margin: 16px 0 48px; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; list-style: none; }}
    .source-status li {{ display: flex; justify-content: space-between; gap: 16px; padding: 12px 14px; border-bottom: 1px solid var(--border); }}
    .source-status span {{ color: var(--muted); }}
    .warnings {{ margin: 0 0 48px; padding: 20px 24px; border: 1px solid #efc49e; border-radius: 12px; background: var(--warning-soft); color: var(--warning); }}
    .warnings h2 {{ margin: 0 0 8px; font-size: 18px; }}
    .warnings ul {{ margin: 0; padding-left: 20px; }}
    .source-section {{ margin-top: 56px; }}
    .section-heading {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 16px; }}
    .section-heading h2 {{ margin: 0; font-size: 28px; letter-spacing: -.02em; }}
    .section-heading span {{ color: var(--muted); font-size: 14px; }}
    .news-grid {{ display: grid; gap: 16px; }}
    .news-card {{ padding: 24px; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }}
    .news-meta {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px 16px; color: var(--muted); font-size: 14px; }}
    .news-card h3 {{ max-width: 760px; margin: 14px 0 8px; font-size: clamp(20px, 3vw, 26px); line-height: 1.3; letter-spacing: -.015em; }}
    .news-card h3 a {{ text-decoration: none; }}
    .news-card h3 a:hover {{ color: var(--accent); text-decoration: underline; text-underline-offset: 3px; }}
    .zh-title {{ max-width: 760px; margin: 0; color: var(--accent); font-size: 18px; font-weight: 650; }}
    .summary {{ max-width: 760px; margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--border); }}
    .summary p {{ margin: 10px 0; }}
    .label {{ display: inline-block; min-width: 52px; margin-right: 8px; color: var(--muted); font-size: 13px; font-weight: 700; }}
    .source-link {{ display: inline-flex; min-height: 44px; align-items: center; margin-top: 12px; color: var(--accent); font-weight: 650; text-decoration: none; }}
    .source-link:hover {{ text-decoration: underline; text-underline-offset: 3px; }}
    .empty-state {{ margin-top: 48px; padding: 64px 24px; text-align: center; border: 1px solid var(--border); border-radius: 12px; background: var(--surface); }}
    .empty-mark {{ margin: 0; color: var(--accent); font-size: 48px; }}
    .empty-state h2 {{ margin: 8px 0; }}
    footer {{ margin-top: 64px; padding-top: 24px; border-top: 1px solid var(--border); color: var(--muted); font-size: 14px; }}
    @media (max-width: 600px) {{
      .page {{ width: min(100% - 32px, 1080px); padding: 32px 0 48px; }}
      .metrics {{ grid-template-columns: 1fr; margin-top: 32px; }}
      .source-status {{ grid-template-columns: 1fr; margin-bottom: 36px; }}
      .source-section {{ margin-top: 40px; }}
      .news-card {{ padding: 20px; }}
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --canvas: #191919;
        --surface: #202020;
        --surface-2: #383836;
        --text: #ffffff;
        --muted: rgba(255,255,255,.65);
        --border: rgba(255,255,255,.20);
        --accent: #5e9fe8;
        --accent-soft: rgba(94,159,232,.12);
        --warning: #de9255;
        --warning-soft: rgba(222,146,85,.12);
      }}
      .warnings {{ border-color: rgba(222,146,85,.45); }}
    }}
    @media (prefers-reduced-motion: reduce) {{ * {{ scroll-behavior: auto !important; }} }}
  </style>
</head>
<body>
  <main class="page">
    <header>
      <p class="eyebrow">Hourly market monitor</p>
      <h1>US Stock News<br><span lang="zh-CN">美股资讯双语快报</span></h1>
      <p class="subtitle">Public headlines and short summaries, translated with Ollama Cloud while preserving the English source.</p>
      <time class="generated" datetime="{escape(generated_at.isoformat(), quote=True)}">Generated / 生成时间：{escape(local_time.strftime('%Y-%m-%d %H:%M:%S %Z'))}</time>
    </header>

    <section class="metrics" aria-label="Digest metrics">
      <div class="metric"><strong>{total_items}</strong><span>New items / 新增资讯</span></div>
      <div class="metric"><strong>{active_sources}</strong><span>Active sources / 有效来源</span></div>
    </section>
    <ul class="source-status" aria-label="Source status">{status_cards}</ul>

    {warning_block}
    {content}

    <footer>
      Public metadata only. No paywall bypass or full-article reproduction.<br>
      <span lang="zh-CN">仅收集公开元数据，不绕过付费墙，也不复制文章全文。</span>
    </footer>
  </main>
</body>
</html>
"""
