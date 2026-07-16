#!/usr/bin/env python3
"""Backfill canonical entities.source_uri from nested attributes.source_uri.

Dry-run by default. Apply only after lead verification and service reload approval.

Usage::

  ~/.venvs/universal/bin/python scripts/cortex/backfill_entity_source_uri_canonical.py
  ~/.venvs/universal/bin/python scripts/cortex/backfill_entity_source_uri_canonical.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "libs"))

from cortex_store.db import cortex_conn  # noqa: E402
from cortex_store.source_uri_backfill import (  # noqa: E402
    DEFAULT_EXPECTED_COUNT,
    SourceUriBackfillCountMismatchError,
    SourceUriBackfillVerificationError,
    run_source_uri_backfill,
    select_stranded_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote nested attributes.source_uri to canonical column"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit repairs (default is dry-run only)",
    )
    parser.add_argument(
        "--expected-count",
        type=int,
        default=DEFAULT_EXPECTED_COUNT,
        help=f"Abort when stranded count differs (default {DEFAULT_EXPECTED_COUNT})",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    with cortex_conn() as conn:
        stranded = select_stranded_rows(conn)
        mode = "dry-run" if dry_run else "apply"
        print(f"## entity source_uri canonical backfill ({mode})")
        print(f"- stranded rows: {len(stranded)}")
        print(f"- expected count: {args.expected_count}")

        try:
            result = run_source_uri_backfill(
                conn,
                dry_run=dry_run,
                expected_count=args.expected_count,
            )
        except SourceUriBackfillCountMismatchError as exc:
            print(f"- status: aborted ({exc})", file=sys.stderr)
            return 1
        except SourceUriBackfillVerificationError as exc:
            print(f"- status: verification_failed ({exc})", file=sys.stderr)
            return 1

    print(f"- repaired: {result.repaired_count}")
    print(f"- residual stranded: {result.residual_count}")
    print(f"- applied: {result.applied}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
