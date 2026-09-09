"""Run the full review verification: baseline, red phase, fixes, final proof.

The script is intentionally strict. It refuses to run on the wrong branch, it
refuses to run if the reviewed baseline was already modified, and it refuses to
write evidence unless the new regression tests first reproduce the defects and
then pass after the guarded fixes are applied.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "f7953bf4e7386efc0008e0578dce7d34c22d25f1"
BRANCH = "fix/report-regressions-20260908"
RESULTS = ROOT / ".test-results"
DOCS = ROOT / "docs"
STAMP = "2026-09-08"

GUARDED_PATHS = (
    "market_report.py",
    "market_report_src",
    "hybrid_data.py",
    "hybrid_runtime.py",
    "bilingual_runtime.py",
    "news_translation.py",
    "premium_translation.py",
    "ai_translation.py",
    "templates",
    "config",
    "russell2000_market_report",
    "hourly_news_bot",
)

CASES = {
    "root.py": "tests/test_review_regressions.py",
    "news.py": "hourly_news_bot/tests/test_review_regressions.py",
    "russell.py": "russell2000_market_report/tests/test_review_regressions.py",
}

LIMITATIONS = (
    "\u53ea\u8fd0\u884c\u79bb\u7ebf\u5355\u5143\u6d4b\u8bd5\u4e0e demo \u6a21\u5f0f\u5192\u70df\uff0c\u672a\u8c03\u7528 Massive\u3001Alpaca\u3001SEC\u3001FINRA\u3001FRED\u3001iShares \u7b49\u5b9e\u65f6\u63a5\u53e3\u3002",
    "\u672a\u8c03\u7528 Ollama \u4e91\u7aef\u7ffb\u8bd1\uff0c\u53cc\u8bed\u94fe\u8def\u4f7f\u7528\u53ef\u63a7\u7684\u6ce8\u5165\u7ffb\u8bd1\u51fd\u6570\u9a8c\u8bc1\u3002",
    "\u672a\u6267\u884c Google Drive \u4e0a\u4f20\uff0c\u4e5f\u672a\u53d1\u5e03 GitHub Pages\u3002",
    "\u672a\u4f7f\u7528\u771f\u5b9e\u6388\u6743\u7684\u5386\u53f2\u6210\u5206\u80a1\u6570\u636e\uff0c\u53ea\u9a8c\u8bc1\u7f3a\u5931\u3001\u672a\u6807\u65e5\u671f\u3001\u665a\u4e8e\u62a5\u544a\u65e5\u7684\u6210\u5206\u4f1a\u88ab\u62d2\u7edd\u3002",
    "PDF \u53ea\u6821\u9a8c\u6587\u672c\u5c42\u5173\u952e\u5b57\u4e0e\u6587\u4ef6\u7ed3\u6784\uff0c\u672a\u505a\u50cf\u7d20\u7ea7\u6392\u7248\u6bd4\u5bf9\u3002",
)

README_SECTION = """## \u79bb\u7ebf\u9a8c\u8bc1\u4e0e\u672c\u6b21\u4fee\u590d\uff082026-09-08\uff09

\u672c\u6b21\u4fee\u590d\u5728 GitHub Actions \u4e0a\u5b8c\u6210\u79bb\u7ebf\u9a8c\u8bc1\uff1a\u5148\u8dd1\u57fa\u7ebf\u5355\u5143\u6d4b\u8bd5\uff0c\u518d\u7528\u65b0\u589e\u56de\u5f52\u6d4b\u8bd5\u590d\u73b0\u7f3a\u9677\uff0c\u6700\u540e\u5e94\u7528\u4fee\u590d\u5e76\u91cd\u8dd1\u5168\u91cf\u6d4b\u8bd5\u4e0e demo \u5192\u70df\u3002

- \u8bc1\u636e\uff1a`docs/verification-2026-09-08.md`\u3001`docs/verification-2026-09-08.json`
- \u79bb\u7ebf\u5355\u5143\u6d4b\u8bd5\uff1a`python scripts/run_offline_tests.py`
- demo \u6a21\u5f0f\u5192\u70df\uff1a`python scripts/smoke_offline_reports.py`
- \u672a\u8986\u76d6\uff1a\u5b9e\u65f6\u884c\u60c5/SEC/FINRA/FRED/iShares \u63a5\u53e3\u3001Ollama \u7ffb\u8bd1\u3001Google Drive \u4e0a\u4f20\u3001GitHub Pages \u53d1\u5e03\u3002
"""

RUSSELL_README_SECTION = """## \u5386\u53f2\u56de\u6eaf\u7684\u6210\u5206\u80a1\u8981\u6c42

\u56de\u6eaf\u8d85\u8fc7\u5bbd\u9650\u5929\u6570\uff08`RUSSELL2000_CONSTITUENTS_GRACE_DAYS`\uff0c\u9ed8\u8ba4 7 \u5929\uff09\u7684\u65e7\u4ea4\u6613\u65e5\u65f6\uff0c\u5fc5\u987b\u901a\u8fc7 `RUSSELL2000_CONSTITUENTS_FILE` \u63d0\u4f9b\u5f53\u65f6\u7684\u6210\u5206\u80a1\u6587\u4ef6\uff1b\u7eaf\u4ee3\u7801\u6e05\u5355\u8fd8\u9700\u8981 `RUSSELL2000_CONSTITUENTS_AS_OF` \u6807\u660e\u57fa\u51c6\u65e5\u671f\u3002IWM \u6700\u65b0\u6301\u4ed3\u53ea\u4ee3\u8868\u4eca\u5929\u7684\u6210\u5206\uff0c\u76f4\u63a5\u7528\u4e8e\u5386\u53f2\u65e5\u671f\u4f1a\u5f15\u5165\u5e78\u5b58\u8005\u504f\u5dee\uff0c\u73b0\u5728\u4f1a\u76f4\u63a5\u62a5\u9519\u800c\u4e0d\u662f\u9759\u9ed8\u4ea7\u51fa\u9519\u8bef\u62a5\u544a\u3002
"""

ENV_BLOCK = """# \u5386\u53f2\u56de\u6eaf\u5fc5\u586b\uff1a\u6388\u6743\u7684\u7f57\u7d20 2000 \u6210\u5206\u80a1\u6587\u4ef6\u8def\u5f84
RUSSELL2000_CONSTITUENTS_FILE=
# \u7eaf\u4ee3\u7801\u6e05\u5355\u9700\u8981\u7684\u6210\u5206\u80a1\u57fa\u51c6\u65e5\u671f\uff08YYYY-MM-DD\uff09
RUSSELL2000_CONSTITUENTS_AS_OF=
# \u5141\u8bb8\u6210\u5206\u80a1\u57fa\u51c6\u65e5\u665a\u4e8e\u62a5\u544a\u65e5\u7684\u5bbd\u9650\u5929\u6570\uff0c\u9ed8\u8ba4 7
RUSSELL2000_CONSTITUENTS_GRACE_DAYS=7
"""


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=str(ROOT), text=True).strip()


def run(command, *, must_pass: bool = True) -> int:
    printable = " ".join(str(value) for value in command)
    print(f"::group::{printable}", flush=True)
    completed = subprocess.run([str(value) for value in command], cwd=str(ROOT), timeout=3600)
    print("::endgroup::", flush=True)
    if must_pass and completed.returncode:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {printable}")
    return completed.returncode


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def totals(report: dict) -> dict:
    return {
        "suites": len(report.get("suites", [])),
        "tests": report.get("total_tests", 0),
        "failures": report.get("total_failures", 0),
        "errors": report.get("total_errors", 0),
        "skipped": report.get("total_skipped", 0),
        "passed": report.get("passed", False),
    }


def append_once(path: Path, block: str, marker: str) -> None:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in text:
        return
    body = block.strip() + "\n"
    path.write_text(f"{text.rstrip()}\n\n{body}" if text.strip() else body, encoding="utf-8")


def check_syntax() -> int:
    parts = sorted((ROOT / "market_report_src").glob("part_*.inc"))
    ast.parse("".join(path.read_text(encoding="utf-8") for path in parts), filename="market_report_src")
    checked = 0
    for file in sorted(ROOT.rglob("*.py")):
        relative = file.relative_to(ROOT)
        if any(part.startswith(".") for part in relative.parts):
            continue
        ast.parse(file.read_text(encoding="utf-8"), filename=str(relative))
        checked += 1
    return checked


def build_markdown(evidence: dict) -> str:
    baseline = evidence["baseline"]
    red = evidence["red_before_fix"]
    after = evidence["after_fix"]
    verdict = "\u901a\u8fc7" if after["passed"] else "\u672a\u901a\u8fc7"
    base_verdict = "\u901a\u8fc7" if baseline["passed"] else "\u672a\u901a\u8fc7"
    lines = [
        f"# \u79bb\u7ebf\u9a8c\u8bc1\u8bb0\u5f55\uff08{STAMP}\uff09",
        "",
        f"- \u5ba1\u9605\u57fa\u7ebf\uff1a`{evidence['baseline_commit']}`",
        f"- \u5206\u652f\uff1a`{evidence['branch']}`",
        f"- CI \u8fd0\u884c\uff1a{evidence['workflow_run']}",
        f"- Python\uff1a{evidence['python']}",
        "",
        "## \u6d4b\u8bd5\u7ed3\u679c",
        "",
        "| \u9636\u6bb5 | \u5957\u4ef6 | \u7528\u4f8b | \u5931\u8d25 | \u9519\u8bef | \u8df3\u8fc7 | \u7ed3\u8bba |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        f"| \u4fee\u590d\u524d\u57fa\u7ebf\u5168\u91cf | {baseline['suites']} | {baseline['tests']} | {baseline['failures']} | {baseline['errors']} | {baseline['skipped']} | {base_verdict} |",
        f"| \u65b0\u589e\u56de\u5f52\uff08\u4fee\u590d\u524d\uff09 | {red['suites']} | {red['tests']} | {red['failures']} | {red['errors']} | {red['skipped']} | \u590d\u73b0\u7f3a\u9677 |",
        f"| \u4fee\u590d\u540e\u5168\u91cf | {after['suites']} | {after['tests']} | {after['failures']} | {after['errors']} | {after['skipped']} | {verdict} |",
        "",
        f"\u4fee\u590d\u524d\u590d\u73b0\u7f3a\u9677\u7684\u5957\u4ef6\uff1a{', '.join(red['failing_suites'])}",
        "",
        f"\u8bed\u6cd5\u68c0\u67e5\uff1a{evidence['python_files_parsed']} \u4e2a Python \u6587\u4ef6\u4e0e\u5408\u5e76\u540e\u7684 market_report_src \u7247\u6bb5\u5747\u53ef\u89e3\u6790\u3002",
        "",
        "## demo \u6a21\u5f0f\u5192\u70df",
        "",
        "```json",
        json.dumps(evidence["smoke"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## \u672a\u8986\u76d6\u8303\u56f4",
        "",
    ]
    lines.extend(f"- {item}" for item in evidence["limitations"])
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    branch = os.environ.get("GITHUB_REF_NAME") or git("branch", "--show-current")
    if branch != BRANCH:
        raise RuntimeError(f"Refusing to run outside {BRANCH} (current branch: {branch})")

    changed = git("diff", "--name-only", BASE, "HEAD", "--", *GUARDED_PATHS)
    if changed:
        raise RuntimeError(f"Reviewed baseline was already modified on this branch: {changed}")

    run(
        [sys.executable, "scripts/run_offline_tests.py", "--report", RESULTS / "baseline.json"],
        must_pass=False,
    )
    baseline = read_json(RESULTS / "baseline.json")

    for source, target in CASES.items():
        destination = ROOT / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "maintenance" / "cases" / source, destination)

    red_status = run(
        [
            sys.executable,
            "scripts/run_offline_tests.py",
            "--pattern",
            "test_review_regressions.py",
            "--report",
            RESULTS / "red-before-fix.json",
        ],
        must_pass=False,
    )
    red = read_json(RESULTS / "red-before-fix.json")
    failing = [suite["name"] for suite in red["suites"] if not suite["passed"]]
    if red_status == 0 or red["total_tests"] < 20 or not failing:
        raise RuntimeError(
            f"New regression tests did not reproduce the reviewed defects: {json.dumps(totals(red))}"
        )
    print(f"Red phase reproduced the defects in: {', '.join(failing)}", flush=True)

    run([sys.executable, "maintenance/apply_review_fixes.py"])

    run([sys.executable, "scripts/run_offline_tests.py", "--report", RESULTS / "final.json"])
    final = read_json(RESULTS / "final.json")
    if not final["passed"]:
        raise RuntimeError("Final offline test run is not green")

    run([sys.executable, "scripts/smoke_offline_reports.py", "--output", RESULTS / "smoke"])
    smoke = read_json(RESULTS / "smoke" / "summary.json")
    parsed = check_syntax()

    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline_commit": BASE,
        "branch": BRANCH,
        "workflow_run": "https://github.com/zencolab/usstock/actions/runs/"
        + os.environ.get("GITHUB_RUN_ID", "local"),
        "python": sys.version.split()[0],
        "python_files_parsed": parsed,
        "baseline": totals(baseline),
        "red_before_fix": {**totals(red), "failing_suites": failing},
        "after_fix": totals(final),
        "smoke": smoke,
        "limitations": list(LIMITATIONS),
    }
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / f"verification-{STAMP}.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = build_markdown(evidence)
    (DOCS / f"verification-{STAMP}.md").write_text(markdown, encoding="utf-8")

    append_once(ROOT / "README.md", README_SECTION, "docs/verification-2026-09-08.md")
    append_once(
        ROOT / "russell2000_market_report" / "README.md",
        RUSSELL_README_SECTION,
        "RUSSELL2000_CONSTITUENTS_GRACE_DAYS",
    )
    for env_file in (ROOT / ".env.example", ROOT / "russell2000_market_report" / ".env.example"):
        if env_file.exists():
            append_once(env_file, ENV_BLOCK, "RUSSELL2000_CONSTITUENTS_AS_OF")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        Path(summary_path).write_text(markdown, encoding="utf-8")
    print(markdown, flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - surfaced as a CI annotation
        print(f"::error::{type(error).__name__}: {error}", flush=True)
        raise SystemExit(1)
