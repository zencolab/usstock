from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = PROJECT_ROOT.parent
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

os.environ.setdefault("MARKET_REPORT_CACHE_DIR", str(PROJECT_ROOT / ".cache"))

import market_report as engine

from russell2000_market_report.runtime import install

install(engine.__dict__, PROJECT_ROOT)
main = engine.main


if __name__ == "__main__":
    raise SystemExit(main())
