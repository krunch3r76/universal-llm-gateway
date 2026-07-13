"""GET /boot-temporal — temporally active, upcoming, resolved, and expired assertions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ...action_hints import detect_expired_unresolved
from ...db import cortex_conn
from ...db import query as db_query
from ...open_items.resolution_index import fetch_resolution_index_records

router = APIRouter(tags=["boot"])

# Shared predicate for auto-expiring, committed-only temporal rows.
# Imported by boot/principal_context.py — keep ONE definition (F4).
_TEMPORAL_ACTIVE_PREDICATE = """
      a.valid_until IS NOT NULL
      AND a.valid_until >= datetime('now')
      AND (a.valid_from IS NULL OR a.valid_from <= datetime('now'))
      AND a.review_status = 'committed'
      AND a.superseded_by IS NULL
      AND (a.resolution_status IS NULL OR a.resolution_status = 'pending')
"""

_TEMPORAL_ACTIVE_SQL = f"""
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim,
           a.valid_from, a.valid_until, a.confidence
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE {_TEMPORAL_ACTIVE_PREDICATE}
    ORDER BY a.valid_until ASC
    LIMIT ?
"""

_TEMPORAL_UPCOMING_SQL = """
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim,
           a.valid_from, a.valid_until, a.confidence
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE a.valid_from IS NOT NULL
      AND a.valid_from > datetime('now')
      AND a.valid_from <= datetime('now', '+7 days')
      AND a.review_status = 'committed'
      AND a.superseded_by IS NULL
      AND (a.resolution_status IS NULL OR a.resolution_status = 'pending')
    ORDER BY a.valid_from ASC
    LIMIT ?
"""

# Recently-resolved work (superseded assertions + closed todos) is sourced via
# open_items.resolution_index so cortex_brief and the control tower share one
# definition of "resolved" — used to suppress stale open_items that still
# reference completed matters as pending.

_TEMPORAL_EXPIRED_UNRESOLVED_SQL = """
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim,
           a.valid_from, a.valid_until, a.confidence
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE a.valid_until IS NOT NULL
      AND a.valid_until < datetime('now')
      AND a.superseded_by IS NULL
      AND a.review_status = 'committed'
      AND (a.resolution_status IS NULL OR a.resolution_status = 'pending')
    ORDER BY a.valid_until DESC
    LIMIT ?
"""


@router.get("/boot-temporal")
def get_boot_temporal(
    active_limit: int = Query(10, ge=1, le=50, description="Max active assertions"),
    upcoming_limit: int = Query(10, ge=1, le=50, description="Max upcoming assertions"),
    resolved_limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Max recently-resolved temporal assertions (last 30 days)",
    ),
    expired_limit: int = Query(
        10,
        ge=1,
        le=50,
        description="Max expired-but-unresolved assertions",
    ),
) -> dict[str, Any]:
    """Temporally active, upcoming, recently-resolved, and expired-unresolved assertions.

    Active: assertions whose validity window includes now (valid_until in
    the future, valid_from in the past or null), not superseded, not resolved.
    Ordered by soonest expiry.

    Upcoming: assertions with valid_from in the next 7 days that haven't
    started yet, not superseded, not resolved. Ordered by soonest start.

    Recently resolved: unified resolved-record set (superseded assertions of any
    type + done/cancelled todos) from the last 30 days, sourced via
    open_items.resolution_index. Used by cortex_brief AND the control tower to
    reconcile stale open_items in session journals that still reference completed
    matters as pending.

    Expired unresolved: assertions with valid_until in the past that are NOT
    superseded. These fell through the cracks — expired but never acted on.
    Returned with action_hints nudging the caller to supersede or resolve.

    Only surfaces assertions with explicit temporal bounds — unbounded
    (valid_until IS NULL) assertions are excluded since they don't expire.
    """
    conn = cortex_conn()
    try:
        active_rows = db_query(conn, _TEMPORAL_ACTIVE_SQL, (active_limit,))
        upcoming_rows = db_query(conn, _TEMPORAL_UPCOMING_SQL, (upcoming_limit,))
        recently_resolved = fetch_resolution_index_records(
            conn, assertion_limit=resolved_limit, todo_limit=resolved_limit
        )
        expired_rows = db_query(
            conn, _TEMPORAL_EXPIRED_UNRESOLVED_SQL, (expired_limit,)
        )
    finally:
        conn.close()

    def _format_row(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": r["id"],
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "claim": r["claim"],
            "valid_from": r["valid_from"],
            "valid_until": r["valid_until"],
            "confidence": r["confidence"],
        }

    expired_formatted = [_format_row(r) for r in expired_rows]
    hints = detect_expired_unresolved(expired_formatted) if expired_formatted else []

    result: dict[str, Any] = {
        "active": [_format_row(r) for r in active_rows],
        "upcoming": [_format_row(r) for r in upcoming_rows],
        "recently_resolved": recently_resolved,
        "expired_unresolved": expired_formatted,
    }
    if hints:
        result["action_hints"] = [h.model_dump() for h in hints]
    return result
