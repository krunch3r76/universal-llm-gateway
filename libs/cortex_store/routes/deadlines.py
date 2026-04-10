from __future__ import annotations

import logging

from fastapi import APIRouter

from ..action_hints import detect_deadline_resolution
from ..db import cortex_conn, query
from ..models import DeadlineItem, DeadlineList

logger = logging.getLogger("cortex-api.deadlines")
router = APIRouter(prefix="/deadlines", tags=["deadlines"])


@router.get("", response_model=DeadlineList)
def list_deadlines() -> DeadlineList:
    """Return the materialized deadlines view used for urgency-aware planning.

    Enriches the response with action_hints when overdue deadlines have
    resolution language in the matter's active assertions.
    """
    conn = cortex_conn()
    try:
        rows = query(conn, "SELECT * FROM matters_with_deadlines")
        items = [DeadlineItem(**row) for row in rows]
        hints = detect_deadline_resolution([item.model_dump() for item in items], conn)
    finally:
        conn.close()

    return DeadlineList(items=items, action_hints=hints or None)
