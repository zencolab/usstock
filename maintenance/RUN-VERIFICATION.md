# 离线验证引导说明

本目录中的脚本只服务于 PR #1 的一次性验证，验证成功后会由 CI 自动删除。

## 流程

1. `scripts/run_offline_tests.py` 跑基线单元测试（禁网、清空所有密钥环境变量），记录到 `.test-results/baseline.json`。
2. 把 `maintenance/cases/*.py` 复制成三个项目的 `tests/test_review_regressions.py`，先跑一次红灯，确认新用例能复现审阅发现的缺陷。
3. `maintenance/apply_review_fixes.py` 应用带唯一锚点校验的修复；任何锚点数量不符都会直接中止，不做模糊替换。
4. 再跑一次全量单元测试和 `scripts/smoke_offline_reports.py`（demo 模式），全部通过后写入 `docs/verification-2026-09-08.md` 与同名 JSON。
5. CI 提交修复结果，并删除 `maintenance/` 与引导工作流；失败时改为提交 `maintenance/logs/run-<id>/` 下的日志与 JSON 以便排查。

## 安全边界

- 不调用 Massive、Alpaca、SEC、FINRA、FRED、iShares 等实时接口。
- 不调用 Ollama 云端翻译。
- 不上传 Google Drive，不发布 GitHub Pages，不合并到 `main`。

触发方式：向本分支推送对本文件或引导工作流的改动。
