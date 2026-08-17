# 美股资讯双语聚合机器人

每小时从 Yahoo Finance、Google Finance、Barron's、CNBC、MarketWatch、Reuters 和 Bloomberg 的公开网页或公开 RSS 收集**标题与短摘要**，通过 Ollama Cloud 的 `gemma4:cloud` 翻译为简体中文，保留中英双语，并上传 Markdown、JSON 与 HTML 到 Google Drive。

> 本项目不会绕过登录、验证码或付费墙，也不会复制或翻译受限文章全文。若来源禁止自动访问，程序会跳过并在报告中记录警告。请同时遵守各网站服务条款和所在地法律。

## 功能

- GitHub Actions 每小时运行一次，默认在每小时第 17 分钟触发。
- 优先使用公开 RSS，失败后才解析公开首页。
- 检查 `robots.txt`，使用低频串行请求，不绕过访问控制。
- 仅处理最近 3 小时的项目，每个来源默认最多 12 条。
- Ollama Cloud 批量翻译标题和短摘要，保留公司名、股票代码、数字和不确定性。
- Google Drive 保存按时间归档的 `.md` / `.json` / `.html`，同时更新 `latest-*` 文件。
- HTML 使用响应式、无脚本、支持深色模式的自包含页面，可直接在浏览器打开。
- Drive 中的 `crawler-state.json` 用于跨 GitHub Actions 运行去重。
- 单个来源失败不会阻止其他来源，错误会写入报告。

## Google Drive 目标

目标账号：`jing50650@gmail.com`

推荐使用 Google Cloud 服务账号：

1. 在 Google Cloud Console 创建项目并启用 **Google Drive API**。
2. 创建服务账号，生成 JSON 密钥。
3. 在 `jing50650@gmail.com` 的 Google Drive 中新建目标文件夹。
4. 将该文件夹以“编辑者”权限共享给 JSON 密钥中的 `client_email`。
5. 打开文件夹，从 URL 中复制文件夹 ID。

仅提供 Gmail 地址无法让 GitHub Actions 直接写入对应 Drive；必须提供已经共享的文件夹 ID和服务账号凭据。

## GitHub 配置

在仓库 **Settings → Secrets and variables → Actions** 中添加：

### Secrets

| 名称 | 内容 |
|---|---|
| `OLLAMA_API_KEY` | Ollama Cloud API Key |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 完整服务账号 JSON，原样粘贴 |
| `GOOGLE_DRIVE_FOLDER_ID` | 已共享目标文件夹的 ID |

### Variables

| 名称 | 默认值 | 说明 |
|---|---|---|
| `OLLAMA_BASE_URL` | `https://ollama.com/api` | 若账号使用其他 Cloud API 地址，请修改 |
| `OLLAMA_MODEL` | `gemma4:cloud` | 必须与 Ollama Cloud 中实际模型标签一致 |
| `REPORT_TIMEZONE` | `UTC` | 例如 `Asia/Shanghai` 或 `Atlantic/Reykjavik` |
| `MAX_AGE_HOURS` | `3` | 仅保留多长时间内的资讯 |
| `MAX_ITEMS_PER_SOURCE` | `12` | 每个来源每次最多条数 |
| `HOURLY_NEWS_ENABLED` | `true` | 设为 `true` 后启用每小时计划任务 |

配置完成后，打开 **Actions → Hourly bilingual US stock news → Run workflow** 手动测试。成功后，定时任务会自动继续运行。GitHub 的计划任务可能比设定时间晚几分钟。

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env
python -m src.main --dry-run
```

只测试特定来源：

```bash
python -m src.main --dry-run --no-translate --source reuters --source bloomberg
```

生成的 Markdown、JSON 和 HTML 位于 `output/`。本地去重状态位于 `state/`，两者都不会提交到 GitHub。

## 维护来源

来源地址和 CSS 选择器位于 `config/sources.yaml`。网站改版、撤销 RSS 或改变访问政策时，更新该文件即可。Barron's 和 Bloomberg 的付费文章只保留公开标题、短摘要与原文链接。

## 安全

- 不要把 API Key 或服务账号 JSON 写入代码、Issue、日志或提交记录。
- 密钥只放在 GitHub Actions Secrets 中。
- 建议为服务账号创建专用 Drive 文件夹，只授予该文件夹的编辑权限。
- 如果密钥意外泄露，立即在 Google Cloud/Ollama 后台撤销并重新生成。
