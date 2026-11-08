#!/usr/bin/env python3
"""Host F3 closeout scan — ``scripts/scan_w4_flagged_closeout.py``."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "libs") not in sys.path:
    sys.path.insert(0, str(_ROOT / "libs"))
_stargate = _ROOT / "services" / "universal-stargate"
if _stargate.is_dir() and str(_stargate) not in sys.path:
    sys.path.insert(0, str(_stargate))

from cortex_store.entity_state_closeout_scan import (  # noqa: E402
    scan_live_w4_flagged_closeout_stats,
)


def main() -> int:
    from cortex_store import db as cortex_db

    path = os.environ.get("CORTEX_DB_PATH", "").strip()
    if path:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    elif cortex_db.check_cortex_db():
        conn = cortex_db.cortex_conn(read_only=True)
    else:
        print(
            "CORTEX DB not reachable; set CORTEX_DB_PATH or start cortex-api DB",
            file=sys.stderr,
        )
        return 1
    try:
        print(scan_live_w4_flagged_closeout_stats(conn))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
