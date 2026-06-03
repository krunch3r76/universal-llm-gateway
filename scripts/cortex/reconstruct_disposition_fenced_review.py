#!/usr/bin/env python3
"""Export Bucket-G reconstruct markers on fenced entity types (thread 1210 T3).

Operator/human review queue — no writes. Contradiction-vs-supplement detection
is manual; this exports all staged reconstruct rows on fenced prefixes where
the entity also has committed (non-staged) assertions.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from reconstruct_constants import MARKER  # noqa: E402

FENCED_PREFIXES = (
    "legal_matter:",
    "account:",
    "estate:",
    "person:",
    "tax_document:",
    "property:",
    "property_tax:",
    "case:",
    "correspondence:",
    "legal_document:",
)

BUCKET_G_FENCED_SQL = """
SELECT a.id, a.entity_id, a.claim, a.confidence, a.review_notes
FROM assertions a
WHERE a.superseded_by IS NULL
  AND a.review_status = 'staged'
  AND a.reviewer = ?
  AND EXISTS (
    SELECT 1 FROM assertions c
    WHERE c.entity_id = a.entity_id
      AND c.superseded_by IS NULL
      AND (c.review_status IS NULL OR c.review_status != 'staged')
  )
ORDER BY a.entity_id, a.id
"""


def is_fenced(entity_id: str) -> bool:
    return entity_id.startswith(FENCED_PREFIXES)


def export(db_path: Path, out_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(BUCKET_G_FENCED_SQL, (MARKER,)).fetchall()]
    conn.close()

    fenced = [r for r in rows if is_fenced(str(r["entity_id"]))]
    by_entity: dict[str, list[int]] = {}
    for r in fenced:
        by_entity.setdefault(str(r["entity_id"]), []).append(int(r["id"]))

    payload = {
        "reviewer": MARKER,
        "note": (
            "Operator review list (1210-T3): Bucket-G staged reconstruct on "
            "fenced entity types; per-row classify — no agent auto-dispose."
        ),
        "assertion_count": len(fenced),
        "entity_count": len(by_entity),
        "entities": [
            {"entity_id": eid, "assertion_ids": ids, "count": len(ids)}
            for eid, ids in sorted(by_entity.items())
        ],
        "rows": [
            {
                "id": int(r["id"]),
                "entity_id": str(r["entity_id"]),
                "claim": str(r["claim"])[:200],
                "confidence": str(r["confidence"]),
            }
            for r in fenced
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "assertion_count": len(fenced),
        "entity_count": len(by_entity),
        "out_path": str(out_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export fenced Bucket-G review list")
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".cortex" / "cortex.db"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output JSON path",
    )
    args = parser.parse_args()
    print(json.dumps(export(Path(args.db), args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
