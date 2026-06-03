#!/usr/bin/env python3
"""Post-052 scoped confidence_band backfill — conservative type defaults.

Sets ``confidence_band`` on NULL rows using birth-path defaults
(unsubstantiated / provisional / confirmed). Idempotent.

Usage::

  ~/.venvs/universal/bin/python scripts/cortex/backfill_confidence_band_scoped.py --dry-run
  ~/.venvs/universal/bin/python scripts/cortex/backfill_confidence_band_scoped.py --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from cortex_store.status_trait_backfill import (  # noqa: E402
    count_null_confidence_band,
    run_scoped_confidence_band_backfill,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scoped confidence_band backfill")
    parser.add_argument("--db", default=_DEFAULT_DB, help="cortex SQLite path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit updates (default is dry-run only)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    candidates = count_null_confidence_band(conn)
    counts = run_scoped_confidence_band_backfill(conn, dry_run=dry_run)

    mode = "dry-run" if dry_run else "applied"
    print(f"## scoped confidence_band backfill ({mode})")
    print(f"- db: {args.db}")
    print(f"- candidates: {candidates}")
    print(f"- entities touched: {counts.entities_touched}")
    print(f"- confidence_band writes: {counts.confidence_band}")
    if counts.by_type:
        print("- by type:")
        for t, n in sorted(counts.by_type.items()):
            print(f"  - {t}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
