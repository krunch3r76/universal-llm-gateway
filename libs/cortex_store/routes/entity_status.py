"""Entity status roll-up — temporally-aware, cross-signal entity state.

Single endpoint that consolidates entity data, assertions, freshness,
session journals, todos, threads, and in-flight operational state into
one response. Eliminates the 4-6 query stitching agents previously needed.
"""

from __future__ import annotations

import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from universal_logging import get_logger

from ..db import cortex_conn, decode_row, json_decode, query
from ..entity_crud import (
    ENTITY_JSON_FIELDS as _ENTITY_JSON_FIELDS,
)
from ..models import AssertionItem, RelationshipItem
from ..observability_bridge import (
    build_in_flight,
    match_threads,
    query_agent_bus_threads,
)
from ..relationship_sql import FROM_CLAUSE as _RELATIONSHIP_FROM
from ..relationship_sql import SELECT_COLUMNS as _RELATIONSHIP_SELECT
from ..status_models import (
    EntityStatusResponse,
    FreshnessInfo,
    SessionMention,
    StatusEntity,
    StatusSummary,
    ThreadReference,
    TodoReference,
)
from .assertions import _ASSERTION_COLS

logger = get_logger("cortex-api.entity_status")
router = APIRouter(tags=["entity_status"])

_ASSERTION_JSON_FIELDS = frozenset({"evidence_uris"})

_STALENESS_THRESHOLDS: list[tuple[float, str]] = [
    (1, "active"),
    (6, "recent"),
    (24, "aging"),
    (72, "stale"),
]


def _staleness_signal(hours: float) -> str:
    for threshold, label in _STALENESS_THRESHOLDS:
        if hours < threshold:
            return label
    return "dormant"


def _parse_ts(ts_str: str | None) -> datetime.datetime | None:
    if not ts_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.datetime.strptime(ts_str, fmt).replace(tzinfo=datetime.UTC)
        except ValueError:
            continue
    return None


def _safe_assertions(rows: list[dict[str, Any]]) -> list[AssertionItem]:
    items: list[AssertionItem] = []
    for row in rows:
        try:
            items.append(AssertionItem(**decode_row(row, _ASSERTION_JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s — deserialization failed",
                row.get("id"),
                exc_info=True,
            )
    return items


@router.get("/entity-status/{entity_id}", response_model=EntityStatusResponse)
def get_entity_status(
    entity_id: str,
    include_historical: bool = Query(False),
    include_threads: bool = Query(True),
) -> EntityStatusResponse:
    """Temporally-aware, cross-signal entity status roll-up.

    Consolidates entity data, active/historical assertions, freshness signals,
    session journal mentions, open todos, agent-bus threads, and (for service
    entities) in-flight operational state from the observability layer.
    """
    now = datetime.datetime.now(tz=datetime.UTC)
    conn = None
    try:
        conn = cortex_conn()
        entities = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {entity_id}",
            )
        entity_row = decode_row(entities[0], _ENTITY_JSON_FIELDS)

        active_rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE entity_id = ? "
            "AND superseded_by IS NULL "
            "AND (valid_until IS NULL OR valid_until > datetime('now')) "
            "ORDER BY created_at DESC",
            (entity_id,),
        )

        historical_rows: list[dict[str, Any]] = []
        if include_historical:
            historical_rows = query(
                conn,
                f"SELECT {_ASSERTION_COLS} FROM assertions WHERE entity_id = ? "
                "AND (superseded_by IS NOT NULL "
                "OR (valid_until IS NOT NULL AND valid_until <= datetime('now'))) "
                "ORDER BY created_at DESC",
                (entity_id,),
            )

        rel_rows = query(
            conn,
            f"SELECT {_RELATIONSHIP_SELECT} {_RELATIONSHIP_FROM} "
            "WHERE r.from_entity = ? OR r.to_entity = ? "
            "ORDER BY r.created_at DESC",
            (entity_id, entity_id),
        )

        journal_rows = query(
            conn,
            "SELECT id, timestamp, agent, summary, decisions "
            "FROM session_journals "
            "WHERE json_array_length(entity_ids) > 0 "
            "AND EXISTS ("
            "  SELECT 1 FROM json_each(entity_ids) WHERE value = ?"
            ") ORDER BY timestamp DESC LIMIT 5",
            (entity_id,),
        )

        entity_slug = entity_id.split(":", 1)[-1] if ":" in entity_id else ""
        todo_rows = query(
            conn,
            "SELECT id, name, "
            "json_extract(attributes, '$.priority') as priority "
            "FROM entities WHERE type = 'todo' AND status = 'confirmed' "
            "AND workflow_state = 'open' "
            "AND json_extract(attributes, '$.domain') = ?",
            (entity_slug,),
        )

        access_rows = query(
            conn,
            "SELECT agent FROM entity_access_log "
            "WHERE entity_id = ? ORDER BY accessed_at DESC LIMIT 1",
            (entity_id,),
        )
    finally:
        if conn:
            conn.close()

    # --- Freshness ---
    last_assertion_at = active_rows[0]["created_at"] if active_rows else None
    last_journal_at = journal_rows[0]["timestamp"] if journal_rows else None
    last_entity_update = entity_row.get("updated_at")
    timestamps = [
        _parse_ts(t)
        for t in [last_assertion_at, last_journal_at, last_entity_update]
        if t
    ]
    most_recent = max(timestamps) if timestamps else None
    staleness_hours = (
        (now - most_recent).total_seconds() / 3600 if most_recent else 999.0
    )
    freshness = FreshnessInfo(
        last_assertion_at=last_assertion_at,
        last_journal_mention_at=last_journal_at,
        last_entity_update_at=last_entity_update,
        last_accessed_by=(access_rows[0]["agent"] if access_rows else None),
        staleness_hours=round(staleness_hours, 1),
        staleness_signal=_staleness_signal(staleness_hours),
    )

    active_assertions = _safe_assertions(active_rows)
    historical_assertions = _safe_assertions(historical_rows)

    outgoing = [
        RelationshipItem(**r) for r in rel_rows if r.get("source_id") == entity_id
    ]
    incoming = [
        RelationshipItem(**r) for r in rel_rows if r.get("target_id") == entity_id
    ]

    recent_sessions: list[SessionMention] = []
    for row in journal_rows:
        decisions = row.get("decisions")
        if isinstance(decisions, str):
            decisions = json_decode(decisions)
        recent_sessions.append(
            SessionMention(
                id=row["id"],
                timestamp=row["timestamp"],
                agent=row["agent"],
                summary=row["summary"],
                decisions=decisions,
            )
        )

    open_todos = [
        TodoReference(id=r["id"], title=r["name"], priority=r.get("priority"))
        for r in todo_rows
    ]

    active_threads: list[ThreadReference] = []
    if include_threads:
        all_threads = query_agent_bus_threads()
        active_threads = match_threads(
            all_threads, entity_row["name"], entity_row.get("aliases")
        )

    in_flight = None
    if entity_row.get("type") == "service":
        in_flight = build_in_flight(entity_row.get("attributes"))

    summary = StatusSummary(
        active_assertion_count=len(active_assertions),
        historical_assertion_count=len(historical_assertions),
        relationship_count=len(outgoing) + len(incoming),
        todo_count=len(open_todos),
        thread_count=len(active_threads),
        session_mention_count=len(recent_sessions),
    )

    return EntityStatusResponse(
        entity=StatusEntity(
            id=entity_row["id"],
            type=entity_row["type"],
            name=entity_row["name"],
            description=entity_row.get("description"),
            status=entity_row.get("status"),
            aliases=entity_row.get("aliases"),
            attributes=entity_row.get("attributes"),
        ),
        freshness=freshness,
        active_assertions=active_assertions,
        historical_assertions=historical_assertions,
        relationships={"outgoing": outgoing, "incoming": incoming},
        recent_sessions=recent_sessions,
        open_todos=open_todos,
        active_threads=active_threads,
        in_flight=in_flight,
        summary=summary,
    )
