#!/usr/bin/env python3
"""Status-trait backfill — hybrid scope C or predicate-equivalence (all types).

**POST-052 (1172-E):** Migration 052 dropped ``entities.status``.  This script
calls :func:`require_entities_status_column` from the backfill lib — it exits 2
with ``entities.status dropped (migration 052); rewrite required (1172-E)`` when
run against a post-DROP cortex DB.  That is the expected P0-fence behaviour; do
not bypass it.

For a read-only trait coverage report on a post-052 DB, use
:func:`cortex_store.status_trait_backfill.run_trait_completeness_scan` directly
or the rewritten cert:
``~/.venvs/universal/bin/python scripts/cortex/trait_fallback_equivalence_cert.py``

Populates NULL ``lifecycle``, ``confidence_band``, and ``adoption`` from legacy
``entities.status`` without mutating ``status``. Idempotent.

Usage::

  ~/.venvs/universal/bin/python scripts/cortex/backfill_status_traits_hybrid.py --dry-run
  ~/.venvs/universal/bin/python scripts/cortex/backfill_status_traits_hybrid.py --apply
  ~/.venvs/universal/bin/python scripts/cortex/backfill_status_traits_hybrid.py \\
      --mode predicate-equivalence --db ~/.cortex/cortex.db --apply

Operator binding:
- hybrid (default): assertion 12020 + scope C (todo:cortex-status-traits-phase2-cutover)
- predicate-equivalence: gpt-5.5 4511b3f6 (todo:cortex-status-traits-phase3-drop-status)
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
    require_entities_status_column,
    run_hybrid_trait_backfill,
    run_predicate_equivalence_trait_backfill,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")


def main() -> int:
    parser = argparse.ArgumentParser(description="Status-trait backfill")
    parser.add_argument("--db", default=_DEFAULT_DB, help="cortex SQLite path")
    parser.add_argument(
        "--mode",
        choices=("hybrid", "predicate-equivalence"),
        default="hybrid",
        help="hybrid=scope-C hot types; predicate-equivalence=all types (4511b3f6)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit trait updates (default is dry-run only)",
    )
    parser.add_argument(
        "--required-only",
        action="store_true",
        help="Hybrid only: limit to todo, decision, agent_skill (exclude plan family)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    require_entities_status_column(conn)

    if args.mode == "predicate-equivalence":
        counts = run_predicate_equivalence_trait_backfill(conn, dry_run=dry_run)
        types_label = "all entity types"
    else:
        types = HOT_TYPES_REQUIRED if args.required_only else HOT_TYPES_DEFAULT
        counts = run_hybrid_trait_backfill(conn, types=types, dry_run=dry_run)
        types_label = ", ".join(sorted(types))

    mode = "dry-run" if dry_run else "applied"
    print(f"## {args.mode} trait backfill ({mode})")
    print(f"- db: {args.db}")
    print(f"- types: {types_label}")
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
