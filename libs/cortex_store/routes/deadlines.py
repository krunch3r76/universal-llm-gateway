from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from ..action_hints import detect_deadline_resolution
from ..db import cortex_conn, query
from ..models import DeadlineItem, DeadlineList

logger = logging.getLogger("cortex-api.deadlines")
router = APIRouter(prefix="/deadlines", tags=["deadlines"])

_RESOLVED_OUTCOMES = frozenset({"defaulted", "met", "withdrawn", "superseded"})


def _is_resolved(item: DeadlineItem) -> bool:
    if item.urgency == "resolved":
        return True
    if item.outcome and item.outcome in _RESOLVED_OUTCOMES:
        return True
    return False


@router.get("", response_model=DeadlineList)
def list_deadlines(
    include_resolved: bool = Query(False, description="Include resolved deadlines"),
) -> DeadlineList:
    """Return active deadlines for urgency-aware planning.

    Excludes deadlines with urgency='resolved' or terminal outcomes
    (defaulted, met, withdrawn, superseded) unless include_resolved=true.
    Enriches the response with action_hints when overdue deadlines have
    resolution language in the matter's active assertions.
    """
    conn = cortex_conn()
    try:
        rows = query(conn, "SELECT * FROM matters_with_deadlines")
        all_items = [DeadlineItem(**row) for row in rows]
        if include_resolved:
            items = all_items
        else:
            items = [i for i in all_items if not _is_resolved(i)]
        hints = detect_deadline_resolution([item.model_dump() for item in items], conn)
    finally:
        conn.close()

    return DeadlineList(items=items, action_hints=hints or None)
