#!/usr/bin/env bash
# shellcheck disable=SC1091
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="${ROOT}/libs:${ROOT}/services/universal-stargate${PYTHONPATH:+:$PYTHONPATH}"
exec "$HOME/.venvs/universal/bin/python" - <<'PY'
"""Host F3 closeout scan — ``scripts/scan_w4_flagged_closeout.py``."""

from __future__ import annotations

import os
import sqlite3
import sys

from cortex_store.entity_state_closeout_scan import scan_live_w4_flagged_closeout_stats


def main() -> int:
    from cortex_store import db as cortex_db

    path = os.environ.get("CORTEX_DB_PATH", "").strip()
    if path:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
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
PY
