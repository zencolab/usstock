from pathlib import Path

_parts_dir = Path(__file__).resolve().parent / "market_report_src"
_source = "".join(path.read_text(encoding="utf-8") for path in sorted(_parts_dir.glob("part_*.inc")))
_entrypoint = '\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'
if not _source.endswith(_entrypoint):
    raise RuntimeError("Unexpected report source entrypoint")
_source = _source[: -len(_entrypoint)]
exec(compile(_source, str(Path(__file__).with_name("market_report_source.py")), "exec"), globals(), globals())

from hybrid_runtime import install as _install_hybrid_runtime

_install_hybrid_runtime(globals())

if __name__ == "__main__":
    raise SystemExit(main())
