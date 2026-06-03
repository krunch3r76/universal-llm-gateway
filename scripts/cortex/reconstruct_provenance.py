#!/usr/bin/env python3
"""T13 provenance reconstruct — attach-or-flag pass (no auto-downgrade).

Reads candidates from cortex DB, attempts resolvable source location, then either:
  ATTACH — POST /assertions/supersede (confidence unchanged, seeded_by marker)
  FLAG   — PATCH review_status=staged (advisory metadata only)

**Disposition filter (CRITICAL — thread 1172 / handoff T19):** Before any batch
PATCH or supersede on *already staged* reconstruct flags, the SQL/API filter MUST
include ``reviewer='reconstruct-2026-06-02'``. Never disposition by
``review_status='staged'`` alone (~7018 rows); reconstruct flags only = **2993**.
Use :data:`reconstruct_constants.STAGED_DISPOSITION_SQL` and
:func:`reconstruct_candidates.verify_reconstruct_staged_disposition_filter`
before mass writes.

See cortex:notes/system/threads/1172-reconstruct-full-population-dispatch.md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from reconstruct_candidates import (  # noqa: E402
    verify_reconstruct_staged_disposition_filter,
)
from reconstruct_constants import (  # noqa: E402
    EXPECTED_RECONSTRUCT_STAGED_COUNT,
    MARKER,
)
from reconstruct_pass import run_pass  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="T13 attach-or-flag reconstruct")
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".cortex" / "cortex.db"),
        help="Path to cortex.sqlite",
    )
    parser.add_argument(
        "--legal-only",
        action="store_true",
        help="Restrict to legal slice entities (37 candidates)",
    )
    parser.add_argument("--entity-id", action="append", dest="entity_ids")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--live", action="store_true", help="Perform writes")
    parser.add_argument(
        "--verify-staged-flags",
        action="store_true",
        help=(
            "Dry-run: assert reconstruct staged flag count equals "
            f"{EXPECTED_RECONSTRUCT_STAGED_COUNT} (reviewer marker required)"
        ),
    )
    parser.add_argument("--session-id", default=f"cursor-{MARKER}")
    parser.add_argument("--agent", default="cursor")
    args = parser.parse_args()

    if args.verify_staged_flags:
        conn = sqlite3.connect(args.db)
        n = verify_reconstruct_staged_disposition_filter(conn)
        conn.close()
        print(
            json.dumps(
                {
                    "reviewer": MARKER,
                    "staged_reconstruct_flags": n,
                    "expected": EXPECTED_RECONSTRUCT_STAGED_COUNT,
                },
                indent=2,
            )
        )
        return 0

    entity_ids = args.entity_ids
    if args.legal_only:
        entity_ids = [
            "case:boe19p-flintridge-appeal-2026",
            "legal_matter:life-insurance-sale",
        ]

    result = run_pass(
        db_path=Path(args.db),
        entity_ids=entity_ids,
        limit=args.limit,
        live=args.live,
        session_id=args.session_id,
        agent=args.agent,
    )

    print(json.dumps({k: v for k, v in result.items() if k != "outcomes"}, indent=2))
    near = [o for o in result["outcomes"] if o.near_miss]
    if near:
        print("\nNear-miss / interesting (first 10):")
        for o in near[:10]:
            print(f"  {o.assertion_id} {o.entity_id}: {o.near_miss}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
