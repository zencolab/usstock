# 罗素 2000 收盘日报

这是 `usstock` 仓库中的独立子项目。它复用原项目经过验证的数据、双语新闻、PDF、Google Drive 和 GitHub Pages 引擎，但拥有独立的入口、缓存、输出目录、测试和 GitHub Actions 工作流。

## 与原项目的唯一选股差异

1. 每次 live 运行先下载并校验 iShares Russell 2000 ETF（IWM）的最新持仓 CSV。
2. 优先用 Massive grouped-daily 行情匹配成分股；若 Massive 因账户权限返回 HTTP 401/403，则自动改用 Alpaca 日线。
3. 再应用与原项目相同的最低股价、最低成交额和代码格式过滤。
4. 按当日复权收盘价相对前一交易日复权收盘价的涨跌幅排序。
5. 输出涨幅前 100 和跌幅前 100，后续半年行情、基本面、股东申报、FINRA、空头持仓、三个月全部新闻与中英翻译流程完全沿用原项目。

## 成分股口径

IWM 旨在跟踪 Russell 2000，本项目使用其官方每日持仓作为可自动化、可审计的实用代理。它不是 FTSE Russell 的授权成分股文件，可能受 ETF 现金、衍生品、再平衡时间和公司行动影响。历史补跑默认使用运行时取得的最新持仓，因此可能存在存活者偏差；如有授权的历史成分文件，可通过 `RUSSELL2000_CONSTITUENTS_FILE` 指定。

程序要求持仓文件解析出 1,500-2,500 个有效股票代码，并要求两个交易日各至少有 1,000 个代码匹配实际排行行情源；否则停止运行，不会退回全市场排行。下载失败时最多使用 14 天内的已校验缓存。

## 行情容错

- 默认优先使用 Massive grouped-daily 数据。
- Massive 返回 HTTP 401/403 时，使用现有 Alpaca 凭据分批下载全部 IWM 成分股的当日和前一交易日日线。
- Alpaca 回退遵循 `ALPACA_FEED`；工作流默认 `iex`，若账户有 SIP 权限可设置为 `sip`。
- 实际排行行情源和每天匹配数量会写入 `metadata.json` 的 `ranking_market_data_by_date` 与 `universe_matched_by_date`。
- Massive 短仓数据无权限时仍按原逻辑降级为空，不影响其余报告生成。

## 自动运行

独立工作流：`.github/workflows/russell2000-daily-report.yml`

- 工作日美东 18:45 运行；
- 支持手动指定交易日、live/demo 和 top N；
- 默认 top N 为 100；
- Artifact 名称为 `russell2000-market-report-*`；
- GitHub Pages 发布到 `/usstock/russell2000/`，不会覆盖原项目首页；
- Drive 文件使用 `russell2000-market-close-*` 前缀，同时上传可直接预览的 PDF、完整 ZIP 和 metadata JSON。

两个报告工作流共用发布并发锁，避免同时写入 `gh-pages`。原项目继续发布在仓库 Pages 根路径，罗素 2000 项目发布在 `russell2000/` 子路径。

## 本地运行

从仓库根目录执行：

```bash
pip install -r requirements.txt
python -m russell2000_market_report.main --mode demo --date 2026-08-10 --top-n 10 \
  --output russell2000_market_report/site \
  --data-output russell2000_market_report/output
```

live 模式沿用根项目的 Repository Secrets / 环境变量：

- `MASSIVE_API_KEY`
- `ALPACA_API_KEY_ID`
- `ALPACA_API_SECRET_KEY`
- `SEC_USER_AGENT`
- `OLLAMA_API_KEY`
- 可选 `OLLAMA_API_KEY_FALLBACK`

Google Drive 沿用 `AGENT_UPLOAD_TOKEN` 或 `DRIVE_GATEWAY_TOKEN`。

## 免责声明

报告仅用于研究，不构成投资建议。IWM 持仓代理与 Russell 2000 官方指数成分可能存在差异；免费或订阅数据的公开展示还需遵守各数据源许可。
