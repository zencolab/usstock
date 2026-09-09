#!/usr/bin/env python3
"""Run every offline test suite in the repository.

Offline means: no market-data API calls, no Ollama calls, no Drive upload and
no GitHub Pages publish. This is the single command used locally and in CI:

    python scripts/run_offline_tests.py

Options:
    --only root|russell|news   Run one suite.
    --regressions              Run only the code-review regression tests.

Suites whose third-party dependencies are missing are reported as SKIPPED
rather than failing the run, so a minimal checkout still validates the
review fixes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUITES = {
    "root": {"cwd": ROOT, "start": "tests", "label": "market report (root)"},
    "russell": {
        "cwd": ROOT,
        "start": "russell2000_market_report/tests",
        "label": "russell 2000",
    },
    "news": {
        "cwd": ROOT / "hourly_news_bot",
        "start": "tests",
        "label": "hourly news bot",
    },
}

REQUIRED_MODULES = {
    "root": ("pandas", "numpy", "jinja2", "requests"),
    "russell": ("pandas",),
    "news": ("bs4", "yaml"),
}


def _missing_modules(names: tuple[str, ...]) -> list[str]:
    import importlib.util

    return [name for name in names if importlib.util.find_spec(name) is None]


def run_suite(name: str, pattern: str) -> str:
    suite = SUITES[name]
    missing = _missing_modules(REQUIRED_MODULES[name])
    if missing:
        print(f"\n=== {suite['label']}: SKIPPED (missing {', '.join(missing)}) ===")
        return "skipped"
    print(f"\n=== {suite['label']} ===", flush=True)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(suite["start"]),
            "-p",
            pattern,
            "-v",
        ],
        cwd=str(suite["cwd"]),
    )
    return "passed" if completed.returncode == 0 else "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=sorted(SUITES), help="Run a single suite")
    parser.add_argument(
        "--regressions",
        action="store_true",
        help="Run only the code-review regression tests",
    )
    args = parser.parse_args()

    pattern = "test_review_regressions.py" if args.regressions else "test_*.py"
    names = [args.only] if args.only else list(SUITES)

    results = {name: run_suite(name, pattern) for name in names}

    print("\n=== summary ===")
    for name, outcome in results.items():
        print(f"{SUITES[name]['label']:<24} {outcome}")
    failed = [name for name, outcome in results.items() if outcome == "failed"]
    if failed:
        print(f"\nFAILED suites: {', '.join(failed)}")
        return 1
    print("\nAll executed suites passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
