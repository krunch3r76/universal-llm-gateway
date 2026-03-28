from __future__ import annotations

import logging

from fastapi import APIRouter

from ..db import cortex_conn, query
from ..models import DeadlineItem, DeadlineList

logger = logging.getLogger("cortex-api.deadlines")
router = APIRouter(prefix="/deadlines", tags=["deadlines"])


@router.get("", response_model=DeadlineList)
def list_deadlines() -> DeadlineList:
    """Return the materialized deadlines view used for urgency-aware planning."""
    conn = cortex_conn()
    try:
        rows = query(conn, "SELECT * FROM matters_with_deadlines")
    finally:
        conn.close()

    return DeadlineList(items=[DeadlineItem(**row) for row in rows])
