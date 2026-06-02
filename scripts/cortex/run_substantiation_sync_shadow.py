#!/usr/bin/env python3
"""Shadow dry-run: substantiation_sync retarget to ``confidence_band``.

Report-only — compares stored ``confidence_band`` vs the D-core binary target
that ``recompute_entity_substantiation_status`` would compute. Does NOT retarget
``substantiation_sync`` writes or flip ``entities.status``.

Usage:
  ~/.venvs/universal/bin/python scripts/cortex/run_substantiation_sync_shadow.py
  ~/.venvs/universal/bin/python scripts/cortex/run_substantiation_sync_shadow.py --db ~/.cortex/cortex.db
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from cortex_store.substantiation_sync_shadow import (  # noqa: E402
    render_markdown,
    run_substantiation_sync_shadow,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Substantiation-sync confidence_band retarget shadow diff"
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="cortex SQLite path")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    report = run_substantiation_sync_shadow(conn, db_path=args.db)
    print(render_markdown(report))
    print(
        "\n[report-only] no DB writes; live hook writes confidence_band only "
        f"(would_change={report.would_change_confidence_band} "
        f"match={report.band_already_matches} "
        f"skipped_field={report.skipped_non_status_confidence_field} "
        f"demotions_blocked={report.would_demote_band})"
    )
    conn.close()
    return 1 if report.missing_trait_columns else 0


if __name__ == "__main__":
    raise SystemExit(main())
