#!/usr/bin/env python3
"""Option (b) — persist host-derived ``assertions.credibility`` for http(s) sources.

Dry-run (default) reports per-host counts; ``--apply`` writes listed/*.gov bands only.
Idempotent. Does NOT retarget substantiation_sync or flip entity status.

Usage:
  ~/.venvs/universal/bin/python scripts/cortex/backfill_assertion_credibility_external.py
  ~/.venvs/universal/bin/python scripts/cortex/backfill_assertion_credibility_external.py --apply

Operator binding: assertion 12013 (a)+(b), todo:cortex-status-traits-phase2-cutover.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from cortex_store.assertion_credibility_backfill import (  # noqa: E402
    run_external_credibility_backfill,
)

_DEFAULT_DB = os.path.expanduser("~/.cortex/cortex.db")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="External http(s) assertion credibility backfill (option b)"
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="cortex SQLite path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit credibility updates (default is dry-run only)",
    )
    args = parser.parse_args()

    dry_run = not args.apply
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    counts = run_external_credibility_backfill(conn, dry_run=dry_run)

    mode = "dry-run" if dry_run else "applied"
    print(f"## external credibility backfill ({mode})")
    print(f"- db: {args.db}")
    print(f"- assertions updated: {counts.assertions_updated}")
    print(f"- distinct http(s) hosts seen: {counts.distinct_http_hosts_seen}")
    print(f"- unlisted http host refs (remain NULL): {counts.unlisted_http_host_refs}")
    if counts.by_band:
        print("- by band:")
        for band, n in sorted(counts.by_band.items()):
            print(f"  - {band}: {n}")
    if counts.by_host:
        print("- by host (listed/*.gov only):")
        for host, n in sorted(counts.by_host.items(), key=lambda x: (-x[1], x[0])):
            print(f"  - {host}: {n}")
    if dry_run and counts.assertions_updated:
        print(
            "- apply: ~/.venvs/universal/bin/python "
            "scripts/cortex/backfill_assertion_credibility_external.py --apply"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
