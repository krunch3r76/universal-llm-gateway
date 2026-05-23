from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Query
from universal_logging import get_logger

from ..action_hints import detect_deadline_resolution
from ..db import cortex_conn, query
from ..models import DeadlineItem, DeadlineList

logger = get_logger("cortex-api.deadlines")
router = APIRouter(prefix="/deadlines", tags=["deadlines"])

_RESOLVED_OUTCOMES = frozenset({"defaulted", "met", "withdrawn", "superseded"})

# Two attribute-key shapes exist on deadline entities:
#   - `$.date` (older deadlines created pre-2026-04)
#   - `$.deadline_date` (newer deadlines, including BOE-19-P 2026-05-05)
# COALESCE both so neither shape silently drops out of the boot card.
#
# Includes deadline_id for the assertion-based resolution filter.
# ∀ entity d with type-relationship 'deadline_for' m: d.id is needed so
# _has_resolved_assertion() can check confirmed RESOLVED assertions on d directly.
#
# UNION arm 2 surfaces todo entities that carry their own `deadline_date`
# attribute (and aren't already routed through a linked deadline entity).
# Per agent-bus thread 882 (option A): one unified ranked list of date-bearing
# items — agents reading the boot don't care about the source-table distinction.
# The matter_id/matter_name cells are repurposed to point at the todo itself
# so the briefing card render shape is unchanged.
_DEADLINES_SQL = """
    SELECT
        m.id AS matter_id,
        m.name AS matter_name,
        d.id AS deadline_id,
        d.name AS deadline_name,
        COALESCE(
            json_extract(d.attributes, '$.deadline_date'),
            json_extract(d.attributes, '$.date')
        ) AS deadline_date,
        json_extract(d.attributes, '$.description') AS deadline_description,
        json_extract(d.attributes, '$.urgency') AS urgency,
        json_extract(d.attributes, '$.outcome') AS outcome
    FROM entities m
    JOIN relationships r ON r.to_entity = m.id AND r.type = 'deadline_for'
    JOIN entities d ON r.from_entity = d.id
    WHERE m.type = 'legal_matter'

    UNION ALL

    SELECT
        t.id AS matter_id,
        t.name AS matter_name,
        t.id AS deadline_id,
        t.name AS deadline_name,
        json_extract(t.attributes, '$.deadline_date') AS deadline_date,
        NULL AS deadline_description,
        json_extract(t.attributes, '$.urgency') AS urgency,
        NULL AS outcome
    FROM entities t
    WHERE t.type = 'todo'
      AND json_extract(t.attributes, '$.deadline_date') IS NOT NULL
      AND (t.workflow_state IS NULL OR t.workflow_state IN ('open', 'in_progress'))

    ORDER BY deadline_date
"""


def _resolved_assertion_ids(conn: sqlite3.Connection, deadline_ids: list[str]) -> frozenset[str]:
    """Return set of deadline_ids that have ≥1 confirmed RESOLVED assertion.

    Belt-and-suspenders: catches ghosts where outcome attribute was never set
    but the agent wrote a confirmed RESOLVED assertion on the deadline entity.
    """
    if not deadline_ids:
        return frozenset()
    placeholders = ",".join("?" * len(deadline_ids))
    rows = query(
        conn,
        f"SELECT DISTINCT entity_id FROM assertions "
        f"WHERE entity_id IN ({placeholders}) "
        f"AND confidence = 'confirmed' "
        f"AND UPPER(claim) LIKE '%RESOLVED%' "
        f"AND superseded_by IS NULL",
        tuple(deadline_ids),
    )
    return frozenset(row["entity_id"] for row in rows)


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

    Excludes deadlines with urgency='resolved', terminal outcomes
    (defaulted, met, withdrawn, superseded), or a confirmed RESOLVED assertion
    on the deadline entity — unless include_resolved=true.
    Enriches the response with action_hints when overdue deadlines have
    resolution language in the matter's active assertions.
    """
    conn = cortex_conn()
    try:
        rows = query(conn, _DEADLINES_SQL)

        deadline_ids = [row["deadline_id"] for row in rows if row.get("deadline_id")]
        resolved_by_assertion = _resolved_assertion_ids(conn, deadline_ids)

        all_items: list[tuple[DeadlineItem, bool]] = []
        for row in rows:
            item = DeadlineItem(**row)
            is_assn_resolved = item.deadline_id in resolved_by_assertion
            all_items.append((item, is_assn_resolved))

        if include_resolved:
            items = [item for item, _ in all_items]
        else:
            items = [
                item
                for item, assn_resolved in all_items
                if not _is_resolved(item) and not assn_resolved
            ]

        hints = detect_deadline_resolution([item.model_dump() for item in items], conn)
    finally:
        conn.close()

    return DeadlineList(items=items, action_hints=hints or None)


def _list_deadlines_impl(*, include_resolved: bool = False) -> dict[str, object]:
    return list_deadlines(include_resolved=include_resolved).model_dump(mode="json")
