#!/usr/bin/env python3
"""Thread 1210 step-1: dismiss staged reconstruct markers on test:agm-* fixtures.

Per lead adjudication (agent-bus 1210 T3): synthetic AGM test scaffolding rows are
not knowledge — reject the reconstruct *marker* (review_status), confidence unchanged.

Filter MUST include reviewer=MARKER (never staged-only ~7018).
Operator gate: default dry-run; pass --live to PATCH.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from reconstruct_constants import MARKER  # noqa: E402
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

AGM_PREFIX = "test:agm-"
DISPOSITION_NOTES = (
    "1210-T3 fixture dismiss: synthetic AGM reconstruct marker; not knowledge. "
    f"reviewer={MARKER}; confidence unchanged per 1172-C."
)
AGM_DISPOSITION_SQL = """
SELECT id, entity_id, claim, confidence, review_status, reviewer
FROM assertions
WHERE superseded_by IS NULL
  AND review_status = 'staged'
  AND reviewer = ?
  AND entity_id LIKE ?
ORDER BY entity_id, id
"""


def load_agm_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(AGM_DISPOSITION_SQL, (MARKER, f"{AGM_PREFIX}%")).fetchall()
    return [dict(r) for r in rows]


def assert_not_staged_only_filter(conn: sqlite3.Connection, agm_count: int) -> None:
    staged_only = int(
        conn.execute(
            "SELECT COUNT(*) FROM assertions WHERE superseded_by IS NULL "
            "AND review_status = 'staged'"
        ).fetchone()[0]
    )
    if agm_count > 0 and staged_only > 5000 and agm_count == staged_only:
        print(
            "WRONG FILTER: agm_count equals all staged rows — missing reviewer marker",
            file=sys.stderr,
        )
        raise SystemExit(2)


def patch_dismiss(client: Any, assertion_id: int) -> None:
    now = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = {
        "review_status": "rejected",
        "reviewer": MARKER,
        "reviewed_at": now,
        "review_notes": DISPOSITION_NOTES,
    }
    r = client.patch(f"/assertions/{assertion_id}", json=body)
    r.raise_for_status()


def run(
    *,
    db_path: Path,
    live: bool,
    manifest_path: Path | None,
    limit: int | None,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    rows = load_agm_rows(conn)
    assert_not_staged_only_filter(conn, len(rows))
    conn.close()

    if limit is not None:
        rows = rows[:limit]

    manifest = {
        "reviewer": MARKER,
        "entity_prefix": AGM_PREFIX,
        "disposition": "reject_marker",
        "review_notes": DISPOSITION_NOTES,
        "count": len(rows),
        "assertion_ids": [int(r["id"]) for r in rows],
        "entities": sorted({str(r["entity_id"]) for r in rows}),
        "live": live,
    }
    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )

    applied = 0
    errors: list[dict[str, Any]] = []
    if live and rows:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=120.0) as client:
            for row in rows:
                aid = int(row["id"])
                try:
                    patch_dismiss(client, aid)
                    applied += 1
                except Exception as exc:
                    errors.append({"assertion_id": aid, "error": str(exc)})

    return {
        "count": len(rows),
        "applied": applied,
        "errors": errors,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "live": live,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dismiss test:agm-* reconstruct staged markers (thread 1210)"
    )
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".cortex" / "cortex.db"),
        help="Path to cortex.sqlite (dry-run id export)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Write assertion id manifest JSON to this path",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--live", action="store_true", help="PATCH review_status=rejected"
    )
    args = parser.parse_args()

    summary = run(
        db_path=Path(args.db),
        live=args.live,
        manifest_path=args.manifest,
        limit=args.limit,
    )
    print(json.dumps(summary, indent=2))
    return 1 if summary.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
