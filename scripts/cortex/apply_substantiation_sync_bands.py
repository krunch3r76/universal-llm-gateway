#!/usr/bin/env python3
"""Production batch apply — substantiation_sync ``confidence_band`` promotions.

Writes ``confidence_band`` only (never ``entities.status``). Shared gating with
``substantiation_sync_gating`` / shadow diff. Fail-closed on band demotions.

Usage:
  ~/.venvs/universal/bin/python scripts/cortex/apply_substantiation_sync_bands.py
  ~/.venvs/universal/bin/python scripts/cortex/apply_substantiation_sync_bands.py --apply
  ~/.venvs/universal/bin/python scripts/cortex/apply_substantiation_sync_bands.py --db ~/.cortex/cortex.db --apply

Operator binding: todo:cortex-status-traits-phase2-cutover (assertion 12106).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from cortex_store.substantiation_sync_batch import (  # noqa: E402
    run_substantiation_sync_batch,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch apply substantiation_sync confidence_band promotions"
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="cortex SQLite path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit band updates (default is dry-run only)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    try:
        counts = run_substantiation_sync_batch(conn, dry_run=dry_run)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        conn.close()
        return 1

    mode = "dry-run" if dry_run else "applied"
    print(f"## substantiation sync batch ({mode})")
    print(f"- db: {args.db}")
    print(f"- total entities: {counts.total_entities}")
    print(f"- promotions: {counts.promotions}")
    print(f"- unchanged: {counts.unchanged}")
    print(f"- skipped: {counts.skipped}")
    print(f"  - non_status_confidence_field: {counts.skipped_non_status_confidence_field}")
    print(f"  - lifecycle_axis: {counts.skipped_lifecycle_axis}")
    print(f"  - adoption_type: {counts.skipped_adoption_type}")
    print(f"  - missing_entity: {counts.skipped_missing_entity}")
    print(f"- demotions_blocked: {counts.demotions_blocked}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
