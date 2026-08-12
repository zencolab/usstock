from pathlib import Path

_parts_dir = Path(__file__).resolve().parent / "market_report_src"
_source = "".join(path.read_text(encoding="utf-8") for path in sorted(_parts_dir.glob("part_*.inc")))
exec(compile(_source, str(Path(__file__).with_name("market_report_source.py")), "exec"), globals(), globals())
