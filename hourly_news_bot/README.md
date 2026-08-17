# 美股资讯双语聚合机器人

每小时从 Yahoo Finance、Google Finance、Barron's、CNBC、MarketWatch、Reuters 和 Bloomberg 的公开网页或 RSS 收集标题与短摘要，通过 Ollama Cloud `gemma4:cloud` 翻译为简体中文，保留中英双语，并将 Markdown、JSON、HTML 上传到 Google Drive。

## 上传方式

项目使用现有 **CNINFO Drive Gateway** Apps Script Web App：

```text
https://script.google.com/macros/s/AKfycbx3JuXMbJOAiiHhUUl3MIQrM2LYlZgCQhkiCNajewFpJxTiRl5cFMXjq45Z_gBqYvHB/exec
```

客户端按照网关的 `run_file` 协议发送：`token`、`run_id`、`sha256`、`file_name`、`mime_type`、`content_base64`。三个报告会保存到：

```text
<BASE_PATH>/runs/YYYY/YYYY-MM/<run_id>/
```

当前 Apps Script 的 `BASE_PATH` 决定最终 Drive 目录。

## GitHub Secrets

在 **Settings → Secrets and variables → Actions** 中配置：

- `OLLAMA_API_KEY`
- `AGENT_UPLOAD_TOKEN`：Apps Script 中新生成的 `AGENT_UPLOAD_TOKEN`；也兼容备用名称 `DRIVE_GATEWAY_TOKEN`

截图中曾显示过的旧 Token 应立即轮换，不能提交到代码库或发送到聊天中。

可选 Variables：

- `OLLAMA_BASE_URL`：默认 `https://ollama.com/api`
- `OLLAMA_MODEL`：默认 `gemma4:cloud`
- `REPORT_TIMEZONE`：默认 `UTC`
- `MAX_AGE_HOURS`：默认 `3`
- `MAX_ITEMS_PER_SOURCE`：默认 `12`

## 自动运行

根目录工作流 `.github/workflows/hourly-bilingual-news.yml` 每小时第 17 分钟运行，也支持手动 `Run workflow`。工作流会：

1. 安装依赖并运行测试；
2. 恢复跨运行去重状态；
3. 抓取并翻译新资讯；
4. 调用网关 `ping`；
5. 通过 `run_file` 上传 `.md`、`.json`、`.html`；
6. 将生成结果额外保存为 GitHub Actions Artifact 7 天。

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 在 .env 中填写 OLLAMA_API_KEY 和 DRIVE_GATEWAY_TOKEN
python -m src.main --dry-run
```

正式上传时去掉 `--dry-run`。

## 安全与访问边界

- 只采集公开标题与短摘要，不绕过登录、验证码或付费墙。
- 不复制或翻译受限文章全文。
- Token 只放在 GitHub Actions Secrets 或本地 `.env`。
- 上传前计算 SHA-256；网关对同一 `run_id + file_name` 的相同内容会跳过重复写入。
