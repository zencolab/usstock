# 美股收盘日报（GitHub Actions）

每天美股常规交易结束后自动生成静态报告，也可在 GitHub **Actions → Run workflow** 手动运行或补跑指定交易日。

## 报告内容

- 纳斯达克综合指数、道琼斯工业指数、标普 500：当日行情和近 6 个月走势。
- 全市场按收盘价相对前收盘价排序：涨幅前 100、跌幅前 100。
- 每只入选股票的近半年价格图、P/E、市值、业务简介、概念标签、最新年报收入、股东/内部人申报摘要、估算换手率。
- FINRA 日度场外卖空成交量图。
- 空头持仓（short interest）图；该数据按监管发布节奏通常每月两次，并不是日度数据。
- CSV/JSON 原始结果随每次 Actions 运行保留 90 天，最新网页发布到 GitHub Pages。

## 数据源

| 数据 | 默认来源 | 说明 |
|---|---|---|
| 全市场日线、个股半年线、公司简介、市值、空头持仓 | Massive（原 Polygon.io） | 需要 API Key；Basic 可运行但 5 次/分钟会较慢，Starter 及以上更适合每日生产 |
| 三大指数 | FRED CSV | `NASDAQCOM`、`DJIA`、`SP500`，无需 Key |
| 收入、EPS、股份数、股权及内部人申报 | SEC EDGAR | 官方公开数据；需设置可识别的 User-Agent |
| 日度卖空成交量 | FINRA Consolidated NMS 文件 | 只覆盖向 FINRA 设施报告的场外交易，不等于全市场空头仓位 |
| 所属概念 | 本仓库 `config/concepts.json` | 基于行业和业务描述的可审计关键词分类，并非交易所官方分类 |

## 重要口径

1. **涨跌幅**：当日复权收盘价 / 上一交易日复权收盘价 - 1。
2. 默认过滤普通格式代码、股价低于 1 美元或日成交额低于 100 万美元的低流动性证券；阈值可在命令行修改。
3. **换手率**：当日成交量 / SEC 最新披露股份数。若拿不到流通股数，报告会明确标为估算值。
4. **P/E**：收盘价 / SEC 最新正的年度摊薄每股收益；亏损或数据缺失显示 `—`。
5. **卖空成交量**与**空头持仓**是两个不同概念。FINRA 日度文件是成交量；short interest 是未平仓空头仓位快照，通常每月发布两次。
6. “股东情况”默认展示最近 13D/13G 受益所有权申报和 Form 4 内部人交易摘要；完整前十大机构股东需要额外的机构持仓数据授权。

## 部署和运行

仓库推送到 `main` 后会自动完成首次构建。工作流同时支持：

- 工作日美东时间 18:30 自动运行；
- **Actions → US market close report → Run workflow** 手动运行；
- 手动指定交易日、`live`/`demo` 模式和涨跌榜数量；
- 未配置 `MASSIVE_API_KEY` 时安全回退到 `demo`，保证首次页面可部署；
- 每次运行保留网页与 CSV/JSON 结果 90 天。

真实行情需要在 **Settings → Secrets and variables → Actions** 配置：

- `MASSIVE_API_KEY`：Massive API Key；
- `SEC_USER_AGENT`：例如 `USMarketCloseReport/1.0 you@example.com`；
- 可选 `MASSIVE_RPM`：API 套餐允许的每分钟请求数，Basic 填 `5`。

计划任务使用 `America/New_York` 时区，因此自动跟随夏令时，并为数据供应商留出收盘后缓冲。交易日判断使用 NYSE 日历；节假日不会把自然日误当成交易日。

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 无 Key 的演示报告
python market_report.py --mode demo --top-n 10 --output site --data-output output

# 真实数据
export MASSIVE_API_KEY="..."
export SEC_USER_AGENT="USMarketCloseReport/1.0 you@example.com"
export MASSIVE_RPM=5
python market_report.py --mode live --top-n 100 --output site --data-output output
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

## 免费层可行性

代码可以使用 Massive Basic，但 200 只股票的半年行情、公司信息及空头数据会触发大量请求，运行时间可能超过 1 小时。正式每日生产建议至少使用无 5 次/分钟限制的股票数据套餐。SEC、FRED、FINRA 部分保持免费。

Yahoo Finance / `yfinance` 没有被用作生产默认源，因为它不是稳定、正式授权的公开 API。
