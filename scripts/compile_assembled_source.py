#!/usr/bin/env python3
"""Byte-compile the assembled ``market_report`` source without executing it.

``market_report.py`` concatenates ``market_report_src/part_*.inc`` at runtime
and executes the result, so a syntax error inside a fragment is invisible to
``python -m py_compile`` (the fragments are not importable modules and are not
valid Python on their own). This script performs the same concatenation and
compiles the assembled source, which turns a broken fragment into a blocking
verification failure instead of a runtime surprise.

Nothing is executed: no market-data API call, no Ollama call, no upload and no
publish. Only :func:`compile` runs.

    python scripts/compile_assembled_source.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "market_report_src"

# market_report.py refuses to run an assembly that does not end with exactly
# this guard, so the invariant is verified here as well.
EXPECTED_TAIL = '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'


def assemble() -> tuple[str, list[Path]]:
    fragments = sorted(SOURCE_DIR.glob("part_*.inc"))
    if not fragments:
        raise SystemExit(f"no source fragments found in {SOURCE_DIR}")
    source = "".join(path.read_text(encoding="utf-8") for path in fragments)
    return source, fragments


def main() -> int:
    source, fragments = assemble()
    print(f"fragments: {', '.join(path.name for path in fragments)}")
    if not source.endswith(EXPECTED_TAIL):
        print(
            "the assembled source does not end with the expected __main__ "
            "guard, so market_report.py would refuse to run it"
        )
        return 1
    try:
        compile(source, "market_report_source.py", "exec")
    except SyntaxError as error:
        print(f"assembled source failed to compile: {error}")
        return 1
    print(f"assembled source compiled: {len(source)} characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
