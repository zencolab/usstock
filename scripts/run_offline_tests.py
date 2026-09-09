"""Run every unit-test suite offline, with network access disabled.

The script never touches live market data, Ollama, Drive or Pages. It only
discovers and runs the repository unit tests in three isolated groups and
writes a machine-readable report to .test-results/.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GROUPS: dict[str, dict[str, object]] = {
    "market": {"cwd": ROOT, "start_dir": "tests", "top_level": "."},
    "russell2000": {"cwd": ROOT, "start_dir": "russell2000_market_report/tests", "top_level": "."},
    "news": {"cwd": ROOT / "hourly_news_bot", "start_dir": "tests", "top_level": "."},
}

DROPPED_ENV = (
    "MASSIVE_API_KEY",
    "MASSIVE_RPM",
    "SEC_USER_AGENT",
    "DRIVE_GATEWAY_URL",
    "DRIVE_GATEWAY_TOKEN",
    "AGENT_UPLOAD_TOKEN",
    "GITHUB_TOKEN",
    "RUSSELL2000_CONSTITUENTS_FILE",
    "RUSSELL2000_CONSTITUENTS_AS_OF",
    "RUSSELL2000_HOLDINGS_URL",
)
DROPPED_PREFIXES = ("ALPACA_", "APCA_", "OLLAMA_")

PACKAGES = (
    "pandas",
    "numpy",
    "Jinja2",
    "requests",
    "pandas-market-calendars",
    "PyYAML",
    "beautifulsoup4",
    "lxml",
)

SITECUSTOMIZE = '''import os

if os.environ.get("USSTOCK_OFFLINE_TESTS") == "1":
    import socket

    MESSAGE = "Network connections are disabled in offline tests"

    def _blocked(*args, **kwargs):
        raise RuntimeError(MESSAGE)

    socket.create_connection = _blocked
    socket.socket.connect = _blocked
    socket.socket.connect_ex = _blocked
'''

RUNNER = '''import json
import sys
import unittest

start_dir, top_level, pattern = sys.argv[1], sys.argv[2], sys.argv[3]
loader = unittest.TestLoader()
suite = loader.discover(start_dir, pattern=pattern, top_level_dir=top_level)
result = unittest.TextTestRunner(verbosity=2, stream=sys.stderr).run(suite)
load_errors = list(getattr(loader, "errors", []) or [])
for error in load_errors:
    print(error, file=sys.stderr)
summary = {
    "tests": result.testsRun,
    "failures": len(result.failures),
    "errors": len(result.errors) + len(load_errors),
    "skipped": len(result.skipped),
    "passed": bool(result.wasSuccessful() and not load_errors),
}
print("SUMMARY_JSON:" + json.dumps(summary))
sys.exit(0 if summary["passed"] else 1)
'''


def build_env(helper_dir: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in DROPPED_ENV and not key.startswith(DROPPED_PREFIXES)
    }
    env["USSTOCK_OFFLINE_TESTS"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{helper_dir}{os.pathsep}{existing}" if existing else str(helper_dir)
    return env


def package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for name in PACKAGES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "missing"
    return versions


def run_group(name: str, pattern: str, env: dict[str, str], runner: Path) -> dict[str, object]:
    spec = GROUPS[name]
    command = [sys.executable, str(runner), str(spec["start_dir"]), str(spec["top_level"]), pattern]
    completed = subprocess.run(
        command,
        cwd=str(spec["cwd"]),
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    output = (completed.stdout or "") + (completed.stderr or "")
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "passed": False}
    for line in output.splitlines():
        if line.startswith("SUMMARY_JSON:"):
            summary = json.loads(line[len("SUMMARY_JSON:") :])
    result = {
        "name": name,
        "cwd": str(spec["cwd"]),
        "start_dir": str(spec["start_dir"]),
        "pattern": pattern,
        "returncode": completed.returncode,
        "output": output[-20000:],
    }
    result.update(summary)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the offline unit-test suites")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--group", action="append", choices=sorted(GROUPS))
    parser.add_argument("--report", type=Path, default=ROOT / ".test-results" / "unit-tests.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    names = args.group or list(GROUPS)
    with tempfile.TemporaryDirectory(prefix="usstock-offline-") as folder:
        helper_dir = Path(folder)
        (helper_dir / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        runner = helper_dir / "offline_runner.py"
        runner.write_text(RUNNER, encoding="utf-8")
        env = build_env(helper_dir)
        suites = [run_group(name, args.pattern, env, runner) for name in names]

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "offline": True,
        "pattern": args.pattern,
        "python": sys.version.split()[0],
        "packages": package_versions(),
        "total_tests": sum(int(suite["tests"]) for suite in suites),
        "total_failures": sum(int(suite["failures"]) for suite in suites),
        "total_errors": sum(int(suite["errors"]) for suite in suites),
        "total_skipped": sum(int(suite["skipped"]) for suite in suites),
        "passed": all(bool(suite["passed"]) for suite in suites),
        "suites": suites,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    for suite in suites:
        print(
            f"{suite['name']}: tests={suite['tests']} failures={suite['failures']} "
            f"errors={suite['errors']} skipped={suite['skipped']} "
            f"passed={suite['passed']}"
        )
        if not suite["passed"]:
            print(str(suite["output"])[-4000:])
    print(
        f"offline unit tests: {report['total_tests']} tests, "
        f"{report['total_failures']} failures, {report['total_errors']} errors -> "
        f"{'PASS' if report['passed'] else 'FAIL'}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
