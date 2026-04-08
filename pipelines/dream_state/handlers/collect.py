"""Collect non-superseded assertions from cortex.db for dream state assessment.

Uses direct SQLite for the batch scan — the cortex-api GET /assertions endpoint
has a 500-item limit which is insufficient for the initial full-corpus run
(~1580 assertions). The cursor-based incremental approach requires ordered
full-table access that isn't exposed via REST.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

logger = logging.getLogger(__name__)

_DB_PATH = os.environ.get(
    "CORTEX_DB_PATH", str(Path.home() / ".cortex" / "cortex.db")
)

_REVIEW_STATUSES = ("committed", "staged", "flagged")


def _collect_from_db(last_id: int | None) -> list[dict[str, Any]]:
    """Scan assertions table for non-superseded items after the cursor."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join("?" for _ in _REVIEW_STATUSES)
        base_query = (
            "SELECT a.id, a.entity_id, a.claim, a.confidence, "
            "a.entrenchment_score, a.review_status, a.review_notes, "
            "a.created_at, a.seeded_by, e.name AS entity_name "
            "FROM assertions a LEFT JOIN entities e ON a.entity_id = e.id "
            "WHERE a.superseded_by IS NULL "
            f"AND a.review_status IN ({placeholders}) "
        )

        if last_id is not None:
            query = base_query + "AND a.id > ? ORDER BY a.id ASC"
            params: tuple[Any, ...] = (*_REVIEW_STATUSES, last_id)
        else:
            query = base_query + "ORDER BY a.id ASC"
            params = _REVIEW_STATUSES

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class CollectHandler(BaseHandler):
    step_type = "dream_state_collect_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        cursor_out = context.get_output("load_cursor")
        last_id: int | None = None
        if cursor_out and cursor_out.json:
            last_id = cursor_out.json.get("last_processed_id")

        batch_size: int = context.get_option("batch_size", 20)
        assertions = _collect_from_db(last_id)

        if not assertions:
            result: dict[str, Any] = {
                "batches": [],
                "total_assertions": 0,
                "batch_count": 0,
                "max_assertion_id": last_id or 0,
            }
            return StepOutput(raw=json.dumps(result), json=result)

        items = [
            {
                "assertion_id": a["id"],
                "entity_id": a.get("entity_id", ""),
                "entity_name": a.get("entity_name", ""),
                "claim": a.get("claim", ""),
                "confidence": a.get("confidence", ""),
                "entrenchment_score": a.get("entrenchment_score") or 0.0,
                "review_status": a.get("review_status", ""),
                "review_notes": a.get("review_notes", ""),
                "created_at": a.get("created_at", ""),
                "session_provenance": a.get("seeded_by", ""),
            }
            for a in assertions
        ]

        batches = [
            items[i : i + batch_size] for i in range(0, len(items), batch_size)
        ]
        max_id = max(a["id"] for a in assertions)

        result = {
            "batches": batches,
            "total_assertions": len(items),
            "batch_count": len(batches),
            "max_assertion_id": max_id,
        }

        logger.info(
            "Dream state collected %d assertions in %d batches (cursor: %s)",
            len(items),
            len(batches),
            last_id,
        )
        return StepOutput(raw=json.dumps(result, default=str), json=result)
