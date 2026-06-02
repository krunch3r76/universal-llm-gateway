#!/usr/bin/env python3
"""Hybrid Phase-2 trait backfill (scope C) — hot entity types only.

Populates NULL ``lifecycle``, ``confidence_band``, and ``adoption`` from legacy
``entities.status`` without mutating ``status``. Idempotent.

Usage:
  ~/.venvs/universal/bin/python scripts/cortex/backfill_status_traits_hybrid.py --dry-run
  ~/.venvs/universal/bin/python scripts/cortex/backfill_status_traits_hybrid.py --db ~/.cortex/cortex.db
  ~/.venvs/universal/bin/python scripts/cortex/backfill_status_traits_hybrid.py --required-only

Operator binding: assertion 12020 ratified + scope C (todo:cortex-status-traits-phase2-cutover).
Do NOT rerun ``run_confidence_shadow.py --persist`` unless hot-type bands are NULL/stale.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from cortex_store.status_trait_backfill import (  # noqa: E402
    HOT_TYPES_DEFAULT,
    HOT_TYPES_REQUIRED,
    run_hybrid_trait_backfill,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hybrid status-trait backfill (scope C)"
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="cortex SQLite path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit trait updates (default is dry-run only)",
    )
    parser.add_argument(
        "--required-only",
        action="store_true",
        help="Limit to todo, decision, agent_skill (exclude plan family)",
    )
    args = parser.parse_args()

    types = HOT_TYPES_REQUIRED if args.required_only else HOT_TYPES_DEFAULT

    dry_run = not args.apply
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    counts = run_hybrid_trait_backfill(conn, types=types, dry_run=dry_run)

    mode = "dry-run" if dry_run else "applied"
    print(f"## hybrid trait backfill ({mode})")
    print(f"- db: {args.db}")
    print(f"- types: {', '.join(sorted(types))}")
    print(f"- entities touched: {counts.entities_touched}")
    print(f"- confidence_band writes: {counts.confidence_band}")
    print(f"- lifecycle writes: {counts.lifecycle}")
    print(f"- adoption writes: {counts.adoption}")
    if counts.by_type:
        print("- by type:")
        for t, n in sorted(counts.by_type.items()):
            print(f"  - {t}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
