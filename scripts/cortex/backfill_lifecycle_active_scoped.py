#!/usr/bin/env python3
"""Post-052 scoped lifecycle backfill — ``lifecycle='active'`` on committed rows.

Sets ``lifecycle='active'`` where NULL and the entity has no non-superseded
assertion with ``review_status='staged'`` (1172 T40 bucket ~3786). Idempotent.

Usage::

  ~/.venvs/universal/bin/python scripts/cortex/backfill_lifecycle_active_scoped.py --dry-run
  ~/.venvs/universal/bin/python scripts/cortex/backfill_lifecycle_active_scoped.py --apply
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from cortex_store.status_trait_backfill import (  # noqa: E402
    count_scoped_lifecycle_active_candidates,
    run_scoped_lifecycle_active_backfill,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scoped lifecycle=active backfill")
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
    candidates = count_scoped_lifecycle_active_candidates(conn)
    counts = run_scoped_lifecycle_active_backfill(conn, dry_run=dry_run)

    mode = "dry-run" if dry_run else "applied"
    print(f"## scoped lifecycle=active backfill ({mode})")
    print(f"- db: {args.db}")
    print(f"- candidates: {candidates}")
    print(f"- entities touched: {counts.entities_touched}")
    print(f"- lifecycle writes: {counts.lifecycle}")
    if counts.by_type:
        print("- by type:")
        for t, n in sorted(counts.by_type.items()):
            print(f"  - {t}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
