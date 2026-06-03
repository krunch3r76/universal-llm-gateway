#!/usr/bin/env python3
"""Export Bucket-G reconstruct markers on non-fenced entity types (thread 1210 T3).

Step-2 review queue for lead/cursor per-row classify (no operator gate).
Read-only export — no PATCH. Scope: service:*, project:*, ai_agent:*.
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
from reconstruct_disposition_fenced_review import (  # noqa: E402
    BUCKET_G_FENCED_SQL,
    is_fenced,
)

NON_FENCED_PREFIXES = ("service:", "project:", "ai_agent:")


def is_non_fenced_target(entity_id: str) -> bool:
    return entity_id.startswith(NON_FENCED_PREFIXES)


def export(db_path: Path, out_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(BUCKET_G_FENCED_SQL, (MARKER,)).fetchall()]
    conn.close()

    target = [
        r
        for r in rows
        if not is_fenced(str(r["entity_id"]))
        and is_non_fenced_target(str(r["entity_id"]))
    ]
    by_entity: dict[str, list[int]] = {}
    for r in target:
        by_entity.setdefault(str(r["entity_id"]), []).append(int(r["id"]))

    payload = {
        "reviewer": MARKER,
        "note": (
            "1210-T3 step-2: Bucket-G staged reconstruct on non-fenced "
            "service/project/ai_agent entities; per-row classify, confidence unchanged."
        ),
        "assertion_count": len(target),
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
            for r in target
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {
        "assertion_count": len(target),
        "entity_count": len(by_entity),
        "out_path": str(out_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export non-fenced Bucket-G review list (1210 step 2)"
    )
    parser.add_argument(
        "--db",
        default=str(Path.home() / ".cortex" / "cortex.db"),
    )
    parser.add_argument("--out", type=Path, required=True, help="Output JSON path")
    args = parser.parse_args()
    print(json.dumps(export(Path(args.db), args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
