# 美股收盘日报（GitHub Actions）

每天美股常规交易结束后自动生成静态报告，也可在 GitHub **Actions → Run workflow** 手动运行或补跑指定交易日。

本版本采用批量混合数据管线，不使用 IBKR、TWS 或 IB Gateway，也不再通过 Massive 逐只查询约 200 只股票。

## 报告内容

- 纳斯达克综合指数、道琼斯工业指数、标普 500：当日行情和近 6 个月走势。
- 全市场按收盘价相对前收盘价排序：涨幅前 100、跌幅前 100。
- 每只入选股票的近半年价格图、P/E、市值、收入、业务/行业说明、概念标签、股东及内部人申报摘要、估算换手率。
- SEC 行业分类保留英文原文，并显示 Ollama Cloud 中文翻译。
- 每只证券展示报表日向前推三个自然月的**全部新闻标题**，不再按关键词或 AI 重要性筛选。
- 新闻目录中的标题全部翻译；点击标题进入站内详情页，查看数据源提供的英文摘要及逐段中文翻译，并可打开英文原文。
- FINRA 日度场外卖空成交量图。
- 空头持仓（short interest）图；该数据按监管发布节奏通常每月两次，并不是日度数据。
- CSV/JSON 原始结果随每次 Actions 运行保留 90 天，最新网页发布到 GitHub Pages。

## 新闻目录口径

1. 日期范围严格使用三个自然月。例如报表日为 `2026-08-14`，范围为 `2026-05-14` 至 `2026-08-14`，首尾日期都包含。
2. Alpaca/Benzinga 新闻接口按证券代码查询，并跟随 `next_page_token` 读取全部分页；不再限制为首批 50 条。
3. 只做日期、证券标签、空标题和重复记录校验；不做重要性评分，也不先按关键词过滤。
4. 股票页显示范围内全部标题，按发布日期从新到旧排列。
5. 详情页显示 API 提供的标题和摘要英汉对照；出于数据授权和版权边界，不复制新闻全文，并始终提供英文原文链接。
6. 新闻抓取缓存按报表日和证券保存；翻译按文本哈希缓存，因此重复新闻不会反复消耗模型额度。

## 快速数据管线

| 数据 | 来源 | 请求方式 |
|---|---|---|
| 全市场当日和前一交易日日线 | Massive（原 Polygon.io） | 仅 2 次 grouped-daily 请求，用于计算完整涨跌榜 |
| 入选股票的半年日线 | Alpaca Market Data | 每批最多 75 只并自动分页，不再逐只请求 |
| 三个月全部新闻目录 | Alpaca News / Benzinga | 每只证券读取完整分页，严格限定三个自然月 |
| 行业、标题和摘要英译中 | Ollama Cloud `gemma4:cloud` | 每批 20 条、跨股票去重并按文本哈希持久缓存 |
| 空头持仓 | Massive | `ticker.any_of` 一次批量查询全部入选股票；不可用时报告明确显示缺失 |
| 三大指数 | FRED CSV | `NASDAQCOM`、`DJIA`、`SP500` |
| 公司名称、CIK、收入、EPS、股份数、行业及申报 | SEC EDGAR | 官方 ticker/CIK 映射；并发请求限制为默认 5 次/秒 |
| 日度卖空成交量 | FINRA Consolidated NMS 文件 | 每个交易日一个全市场文件，本地缓存后只下载新增日期 |
| 所属概念 | `config/concepts.json` | 基于 SEC 行业和说明的可审计关键词分类 |

正常情况下，Massive 免费层每次报告只需要约 3 次请求；主要价格历史由 Alpaca 批量接口完成。

## 重要口径

1. **涨跌幅**：Massive 当日复权收盘价 / 上一交易日复权收盘价 - 1。
2. 默认过滤普通格式代码、股价低于 1 美元或日成交额低于 100 万美元的低流动性证券。
3. **换手率**：当日成交量 / SEC 最新披露股份数；缺少流通股数据时显示为估算或 `—`。
4. **P/E**：收盘价 / SEC 最新正的年度摊薄每股收益；亏损或数据缺失显示 `—`。
5. **市值**：收盘价 × SEC 最新披露股份数，属于估算值。
6. FINRA 日度卖空成交量与 short interest 是两个不同概念。
7. “股东情况”展示最近 13D/13G 受益所有权申报和 Form 3/4/5 内部人申报摘要。
8. SEC 未提供详细业务简介时，页面退回显示 SEC 行业分类，而不会生成虚构业务说明。
9. 新闻目录不是“重要新闻推荐”，而是指定日期范围内数据源返回的完整证券关联标题。
10. 中文行业、标题和摘要由 Ollama Cloud 自动生成；页面始终保留英文内容，中文可能存在术语或语义偏差。

## Repository secrets

真实运行需要在 **Settings → Secrets and variables → Actions → Repository secrets** 添加：

- `MASSIVE_API_KEY`：Massive 免费或付费 API Key；
- `ALPACA_API_KEY_ID`：Alpaca Market Data Key ID；
- `ALPACA_API_SECRET_KEY`：与上述 Key ID 配套的 Secret Key；
- `SEC_USER_AGENT`：例如 `USMarketCloseReport/4.0 you@example.com`；
- `OLLAMA_API_KEY`：Ollama Cloud 主凭据；
- 可选 `MASSIVE_RPM`：Massive Basic 填 `5`；
- 可选 `OLLAMA_API_KEY_FALLBACK`：主凭据返回 `401/402/403/429` 时才尝试的备用凭据。

### 关于备用 Ollama token

程序支持一个备用 token，但不会轮流消耗多个账号：

- 正常情况下始终使用 `OLLAMA_API_KEY`；
- 只有主凭据无效、套餐/额度拒绝或限流时，才尝试 `OLLAMA_API_KEY_FALLBACK`；
- 两个凭据都不会写入网页、Artifact 或日志；失败日志会再次脱敏；
- 备用账号必须由你合法持有或获授权使用，并符合 Ollama 的套餐和服务条款；不要将多账号轮换作为规避免费计划限制的手段。

如果翻译量长期超过额度，更稳妥的方案是升级套餐、减少翻译范围，或迁移到你自己控制的 Ollama 服务。

可选 Repository variables：

- `ALPACA_FEED`：默认 `iex`；只有账户具备 SIP 历史权限时才改为 `sip`；
- `ALPACA_NEWS_RPM`：默认 `180`；
- `ALPACA_NEWS_MAX_PAGES`：默认 `100`，只用于防止异常分页循环；
- `SEC_RPS`：默认 `5`，不要超过 SEC Fair Access 限制；
- `OLLAMA_MODEL`：默认 `gemma4:cloud`；
- `OLLAMA_RPM`：默认 `20`。

> live 模式缺少必要凭据时会明确失败并保留上一份报告，不会静默发布 `UP001`、`DN001` 等 demo 占位数据。Demo 只能通过手动运行并显式选择 `mode=demo` 生成。

## 自动和手动运行

- 工作日美东时间 18:30 自动运行；
- **Actions → US market close report → Run workflow** 手动运行；
- 可指定交易日、`live`/`demo` 模式和涨跌榜数量；
- NYSE 日历负责交易日和节假日判断；
- 每次运行先执行单元测试、离线 demo smoke test和占位符校验；
- live 报告若出现 demo 占位符、缺少完整新闻目录元数据或缺少双语详情页，验证会失败且不会发布。

首次运行需要翻译完整新闻目录，耗时和 Ollama 用量会高于以前的 5 条新闻模式。后续运行会复用新闻及翻译缓存，通常明显更快。

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 无凭据的离线演示
python market_report.py --mode demo --top-n 10 --output site --data-output output

# 真实数据
export MASSIVE_API_KEY="..."
export MASSIVE_RPM=5
export ALPACA_API_KEY_ID="..."
export ALPACA_API_SECRET_KEY="..."
export ALPACA_FEED=iex
export SEC_USER_AGENT="USMarketCloseReport/4.0 you@example.com"
export SEC_RPS=5
export TRANSLATION_PROVIDER=ollama
export OLLAMA_BASE_URL=https://ollama.com/api
export OLLAMA_API_KEY="..."
export OLLAMA_MODEL=gemma4:cloud
export OLLAMA_RPM=20
python market_report.py --mode live --top-n 100 --output site --data-output output
python scripts/validate_output.py site --expected-min 100 --expected-mode live
```

指定历史交易日：

```bash
python market_report.py --mode live --date 2026-08-10 --top-n 100
```

## 输出结构

```text
site/
  index.html
  stocks/AAPL.html
  news/AAPL-2026-08-10-xxxxxxxxxxxx.html
  assets/style.css
  metadata.json
output/
  YYYY-MM-DD/
    indices.csv
    gainers.csv
    losers.csv
    stock_details.json
    finra_short_volume.csv
    short_interest.csv
```

## 数据授权提示

免费 API 并不自动授予公开再分发权。当前 GitHub Pages 是公开网页；正式长期公开展示前，请确认 Massive、Alpaca/Benzinga及其他数据源套餐允许相应的个人展示或再分发用途。新闻页只展示标题和数据源摘要并链接英文原文，不复制完整文章。SEC、FINRA和FRED仍需遵守各自使用政策。
