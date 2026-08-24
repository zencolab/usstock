# 美股资讯双语聚合机器人

每两小时从 Yahoo Finance、Google Finance、Barron's、CNBC、MarketWatch、Reuters 和 Bloomberg 的公开网页或 RSS 收集标题与短摘要，通过 Ollama Cloud `gemma4:cloud` 翻译为简体中文，保留中英双语，并将可直接预览的 PDF、Markdown 和 JSON 上传到 Google Drive。项目不再生成、上传或保留 HTML 报告。

## 上传方式

项目使用现有 **CNINFO Drive Gateway** Apps Script Web App：

```text
https://script.google.com/macros/s/AKfycbx3JuXMbJOAiiHhUUl3MIQrM2LYlZgCQhkiCNajewFpJxTiRl5cFMXjq45Z_gBqYvHB/exec
```

客户端按照网关的 `us_stock_news_file` 协议发送：`token`、`run_id`、`sha256`、`file_name`、`mime_type`、`content_base64`。三个文件会保存到现有兼容目录：

```text
美股资讯/每小时新闻/runs/YYYY/YYYY-MM/<run_id>/
```

其中 `.pdf` 可以直接在 Google Drive 中预览；`.md` 和 `.json` 用于归档及机器读取。现有 Drive 文件夹名称为兼容网关配置而保留，不代表运行频率。

## GitHub Secrets

在 **Settings → Secrets and variables → Actions** 中配置：

- `OLLAMA_API_KEY`
- `AGENT_UPLOAD_TOKEN`：Apps Script 中配置的 `AGENT_UPLOAD_TOKEN`；也兼容备用名称 `DRIVE_GATEWAY_TOKEN`

测试环境可继续使用现有 Token；生产环境请轮换已公开的 Token，且不要提交到代码库或聊天中。

可选 Variables：

- `OLLAMA_BASE_URL`：默认 `https://ollama.com/api`
- `OLLAMA_MODEL`：默认 `gemma4:cloud`
- `REPORT_TIMEZONE`：默认 `UTC`
- `MAX_AGE_HOURS`：默认 `3`
- `MAX_ITEMS_PER_SOURCE`：默认 `12`

本地还可通过 `CHROME_BIN` 指定 Chrome/Chromium 可执行文件。

## 自动运行

根目录工作流 `.github/workflows/hourly-bilingual-news.yml` 每两小时的第 17 分钟运行，也支持手动 `Run workflow`。工作流会：

1. 安装依赖，检查 Chrome，并安装中文 PDF 字体；
2. 运行测试并恢复跨运行去重状态；
3. 抓取和翻译新资讯；
4. 通过无头 Chrome 生成 A4 PDF；
5. 验证输出中包含有效 PDF 且不包含 HTML；
6. 调用网关上传 `.pdf`、`.md` 和 `.json`；
7. 将相同结果保存为 GitHub Actions Artifact 7 天。

## 本地运行

本地需要安装 Chrome 或 Chromium：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 在 .env 中填写 OLLAMA_API_KEY 和 DRIVE_GATEWAY_TOKEN
python -m src.main --dry-run
```

正式上传时去掉 `--dry-run`。每次运行的展示文件只有 PDF，不会在 `output/` 中写入 HTML；用于打印的临时页面会在 PDF 生成后立即删除。

## 安全与访问边界

- 只采集公开标题与短摘要，不绕过登录、验证码或付费墙。
- 不复制或翻译受限文章全文。
- Token 只放在 GitHub Actions Secrets 或本地 `.env`。
- 上传前计算 SHA-256；网关对同一 `run_id + file_name` 的相同内容会跳过重复写入。
