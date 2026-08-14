# 美股收盘日报（GitHub Actions）

每天美股常规交易结束后自动生成静态报告，也可在 GitHub **Actions → Run workflow** 手动运行或补跑指定交易日。

本版本采用批量混合数据管线，不使用 IBKR、TWS 或 IB Gateway，也不再通过 Massive 逐只查询 200 只股票。

## 报告内容

- 纳斯达克综合指数、道琼斯工业指数、标普 500：当日行情和近 6 个月走势。
- 全市场按收盘价相对前收盘价排序：涨幅前 100、跌幅前 100。
- 每只入选股票的近半年价格图、P/E、市值、收入、业务/行业说明、概念标签、股东及内部人申报摘要、估算换手率。
- SEC 行业分类同时保留英文原文，并增加中文AI机器翻译。
- 每只证券筛选近三个月最多 5 条重要新闻，保留英文标题/摘要，并逐段显示中文AI翻译和英文原文链接。
- FINRA 日度场外卖空成交量图。
- 空头持仓（short interest）图；该数据按监管发布节奏通常每月两次，并不是日度数据。
- CSV/JSON 原始结果随每次 Actions 运行保留 90 天，最新网页发布到 GitHub Pages。

## 快速数据管线

| 数据 | 来源 | 请求方式 |
|---|---|---|
| 全市场当日和前一交易日日线 | Massive（原 Polygon.io） | 仅 2 次 grouped-daily 请求，用于计算完整涨跌榜 |
| 200 只入选股票的半年日线 | Alpaca Market Data | 每批最多 75 只并自动分页，不再逐只请求 |
| 近三个月重要新闻 | Alpaca News / Benzinga | 每只证券最多读取 50 条候选，按财报、并购、监管、融资等事件评分后展示最多 5 条 |
| 英译中 | Argos Translate 本地神经翻译模型 | GitHub Actions 首次下载模型，翻译结果按文本哈希缓存；不需要额外AI API Key |
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
9. 新闻“重要性”按财报、业绩指引、并购、监管、融资、重大合同、管理层变动等关键词与时间综合评分，不代表投资建议。
10. 中文行业及新闻翻译由本地神经机器翻译模型自动生成；页面始终保留英文原文，中文可能存在术语或语义偏差。

## Repository secrets

真实运行需要在 **Settings → Secrets and variables → Actions → Repository secrets** 添加：

- `MASSIVE_API_KEY`：Massive 免费或付费 API Key；
- `ALPACA_API_KEY_ID`：Alpaca Market Data Key ID；
- `ALPACA_API_SECRET_KEY`：与上述 Key ID 配套的 Secret Key；
- `SEC_USER_AGENT`：例如 `USMarketCloseReport/2.0 you@example.com`；
- 可选 `MASSIVE_RPM`：Massive Basic 填 `5`。

可选 Repository variables：

- `ALPACA_FEED`：默认 `iex`，适用于免费账户；只有账户具备 SIP 历史权限时才改为 `sip`。IEX 覆盖口径不等同全市场综合行情；
- `SEC_RPS`：默认 `5`，不要超过 SEC Fair Access 限制。

> 工作流在 live 模式缺少凭据时会明确失败并保留上一份报告，不再静默发布 `UP001`、`DN001` 等 demo 占位数据。Demo 只能通过手动运行并显式选择 `mode=demo` 生成。

## 获取 Alpaca 凭据

1. 注册并登录 Alpaca；
2. 创建免费 Market Data / Paper API 凭据；
3. 将 Key ID 保存为 `ALPACA_API_KEY_ID`；
4. 将 Secret Key 保存为 `ALPACA_API_SECRET_KEY`；
5. 不要把凭据写入仓库、Issue、日志或聊天。

免费权限和数据 feed 可能随账户地区及套餐变化。当前默认使用 `ALPACA_FEED=iex`；若账户以后取得 SIP 历史权限，可将 Repository variable 改为 `sip`。

## 自动和手动运行

- 工作日美东时间 18:30 自动运行；
- **Actions → US market close report → Run workflow** 手动运行；
- 可指定交易日、`live`/`demo` 模式和涨跌榜数量；
- NYSE 日历负责交易日和节假日判断；
- 每次运行先执行离线 demo smoke test和占位符校验；
- live 报告若出现 `UP001`、`DN001` 或“演示公司”，验证会失败且不会发布。

预计耗时取决于网络和 SEC/FINRA 数据量：

- 第一次运行通常约 10～30 分钟（包含新闻抓取和英译中模型下载）；
- FINRA 缓存建立后通常约 5～15 分钟。

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
export SEC_USER_AGENT="USMarketCloseReport/2.0 you@example.com"
export SEC_RPS=5
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

免费 API 并不自动授予公开再分发权。当前 GitHub Pages 是公开网页；正式长期公开展示前，请确认 Massive、Alpaca/Benzinga及其他数据源套餐允许相应的个人展示或再分发用途。新闻页只展示标题和摘要并链接英文原文，不复制完整文章。SEC、FINRA和FRED仍需遵守各自使用政策。
