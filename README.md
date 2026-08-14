# 美股收盘日报（GitHub Actions）

每天美股常规交易结束后自动生成静态报告，也可在 GitHub **Actions → Run workflow** 手动运行或补跑指定交易日。

本版本采用批量混合数据管线，不使用 IBKR、TWS 或 IB Gateway，也不再通过 Massive 逐只查询 200 只股票。

## 快速数据管线

| 数据 | 来源 | 请求方式 |
|---|---|---|
| 全市场当日和前一交易日日线 | Massive | 仅 2 次 grouped-daily 请求，用于完整涨跌榜 |
| 200 只入选股票的半年日线 | Alpaca | 每批最多 75 只并自动分页，不再逐只请求 |
| 空头持仓 | Massive | `ticker.any_of` 一次批量查询；不可用时明确显示缺失 |
| 三大指数 | FRED | `NASDAQCOM`、`DJIA`、`SP500` |
| 公司名称、收入、EPS、股份数、行业及申报 | SEC EDGAR | 官方 ticker/CIK 映射，默认限制 5 次/秒 |
| 日度卖空成交量 | FINRA | 每个交易日一个全市场文件并缓存 |

正常情况下，Massive 免费层每次报告只需约 3 次请求；主要价格历史由 Alpaca 批量接口完成。第一次运行预计约 10～30 分钟，缓存建立后约 5～15 分钟。

## Repository secrets

在 **Settings → Secrets and variables → Actions → Repository secrets** 添加：

- `MASSIVE_API_KEY`
- `ALPACA_API_KEY_ID`
- `ALPACA_API_SECRET_KEY`
- `SEC_USER_AGENT`，例如 `USMarketCloseReport/2.0 you@example.com`
- 可选 `MASSIVE_RPM`，免费层填 `5`

可选 Repository variables：

- `ALPACA_FEED`：默认 `sip`；若账户没有延迟 SIP 历史权限可改为 `iex`，但 IEX 口径不等同全市场综合行情。
- `SEC_RPS`：默认 `5`。

工作流在 live 模式缺少凭据时会失败并保留上一份报告，不再静默发布 `UP001`、`DN001` 或“演示公司”。Demo 只能在手动运行时显式选择。

## 运行和输出

- 工作日美东时间 18:30 自动运行；
- 支持手动选择交易日、`live`/`demo` 和榜单数量；
- live 发布前会校验模式、股票页数量、数据源及演示占位符；
- Actions artifact 保留网页及 CSV/JSON 90 天；
- GitHub Pages 发布最新报告。

```bash
export MASSIVE_API_KEY="..."
export ALPACA_API_KEY_ID="..."
export ALPACA_API_SECRET_KEY="..."
export ALPACA_FEED=sip
export SEC_USER_AGENT="USMarketCloseReport/2.0 you@example.com"
python market_report.py --mode live --top-n 100 --output site --data-output output
python scripts/validate_output.py site --expected-min 100 --expected-mode live
```

输出包含 `indices.csv`、`gainers.csv`、`losers.csv`、`stock_details.json`、`finra_short_volume.csv` 和 `short_interest.csv`。

## 口径和授权

- 涨跌幅使用 Massive 当日复权收盘价与前一交易日复权收盘价。
- P/E、市值和换手率由行情与 SEC 披露数据估算。
- SEC 没有详细业务简介时显示 SEC 行业分类，不生成虚构说明。
- FINRA卖空成交量不等于空头持仓。
- 免费 API 不自动授予公开再分发权；长期公开展示前请确认相关套餐许可。
