"""GET /boot-sections — salience-driven entity sections for boot briefings."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from ..db import cortex_conn
from ..db import query as db_query
from ..salience import SalienceResult, compute_all_salience

logger = logging.getLogger("cortex-api.boot")
router = APIRouter(tags=["boot"])


def _relative_time(iso_str: str | None, now: datetime) -> str:
    """Format an ISO timestamp as a human-readable relative time."""
    if not iso_str:
        return "unknown"
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta_s = (now - ts).total_seconds()
        if delta_s < 0:
            return "just now"
        if delta_s < 3600:
            return f"{int(delta_s / 60)}m ago"
        if delta_s < 86400:
            return f"{int(delta_s / 3600)}h ago"
        return f"{int(delta_s / 86400)}d ago"
    except (ValueError, TypeError):
        return "unknown"


def _render_full_section(
    r: SalienceResult,
    assertions: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    now: datetime,
) -> str:
    """Render a full entity section as Markdown."""
    last_active = "unknown"
    if assertions:
        latest = assertions[0].get("observed_at") or assertions[0].get("created_at")
        last_active = _relative_time(latest, now)

    header = f"### {r.entity_name} ({r.entity_type})"
    if r.domain:
        header += f" — {r.domain}"

    lines = [
        header,
        f"**Salience**: {r.salience_score:.2f} | **Last active**: {last_active}",
        "",
    ]

    for a in assertions:
        conf = a.get("confidence", "?")
        lines.append(f"- [{conf}] {a.get('claim', '')}")

    if relationships:
        connected: list[str] = []
        for rel in relationships:
            if rel["from_entity"] == r.entity_id:
                name = rel.get("target_name") or rel["to_entity"]
            else:
                name = rel.get("source_name") or rel["from_entity"]
            rel_desc = rel.get("type_name")
            connected.append(f"{name} ({rel_desc})" if rel_desc else name)
        lines.append(f"\n**Connected**: {', '.join(connected)}")

    return "\n".join(lines)


def _build_full_sections(
    conn: sqlite3.Connection,
    results: list[SalienceResult],
    now: datetime,
) -> list[dict[str, Any]]:
    """Fetch assertions/relationships and render full sections."""
    sections: list[dict[str, Any]] = []
    for r in results:
        assertions = db_query(
            conn,
            "SELECT entity_id, claim, confidence, evidence, "
            "observed_at, created_at FROM assertions "
            "WHERE entity_id = ? AND superseded_by IS NULL "
            "ORDER BY created_at DESC LIMIT 5",
            (r.entity_id,),
        )
        rel_rows = db_query(
            conn,
            "SELECT r.from_entity, r.to_entity, r.type, "
            "rt.description AS type_name, "
            "se.name AS source_name, te.name AS target_name "
            "FROM relationships r "
            "LEFT JOIN relationship_types rt ON rt.type = r.type "
            "LEFT JOIN entities se ON se.id = r.from_entity "
            "LEFT JOIN entities te ON te.id = r.to_entity "
            "WHERE r.from_entity = ? OR r.to_entity = ? "
            "ORDER BY COALESCE(r.strength, 0) DESC LIMIT 5",
            (r.entity_id, r.entity_id),
        )
        sections.append(
            {
                "entity_id": r.entity_id,
                "entity_name": r.entity_name,
                "entity_type": r.entity_type,
                "salience_score": round(r.salience_score, 3),
                "surprise": round(r.surprise, 3),
                "domain": r.domain,
                "section_markdown": _render_full_section(r, assertions, rel_rows, now),
            }
        )
    return sections


def _build_oneline_sections(
    conn: sqlite3.Connection,
    results: list[SalienceResult],
    now: datetime,
) -> list[dict[str, Any]]:
    """Build one-line summary entries with batch queries."""
    if not results:
        return []

    entity_ids = [r.entity_id for r in results]
    placeholders = ",".join("?" * len(entity_ids))

    count_rows = db_query(
        conn,
        f"SELECT entity_id, COUNT(*) AS cnt FROM assertions "
        f"WHERE entity_id IN ({placeholders}) GROUP BY entity_id",
        tuple(entity_ids),
    )
    assertion_counts = {r["entity_id"]: r["cnt"] for r in count_rows}

    activity_rows = db_query(
        conn,
        f"SELECT entity_id, "
        f"MAX(COALESCE(observed_at, created_at)) AS last_active "
        f"FROM assertions WHERE entity_id IN ({placeholders}) "
        f"GROUP BY entity_id",
        tuple(entity_ids),
    )
    last_activities = {r["entity_id"]: r["last_active"] for r in activity_rows}

    sections: list[dict[str, Any]] = []
    for r in results:
        cnt = assertion_counts.get(r.entity_id, 0)
        last_active = _relative_time(last_activities.get(r.entity_id), now)
        sections.append(
            {
                "entity_id": r.entity_id,
                "entity_name": r.entity_name,
                "entity_type": r.entity_type,
                "salience_score": round(r.salience_score, 3),
                "summary": (
                    f"**{r.entity_name}** ({r.entity_type}): "
                    f"Last active {last_active} | {cnt} assertions"
                ),
            }
        )
    return sections


def _advance_slow_state(conn: sqlite3.Connection) -> int:
    """Advance slow_state_hash to fast_state_hash for all cached entities.

    Boot is the observation point. Resets last_surprise=0 and
    boot_section_cache='one_line' so next boot's cache hits reflect the
    advanced observation: unchanged entities show surprise=0, only entities
    with new assertions/relationships (fingerprint change) trigger recompute
    with meaningful surprise on the next boot.
    """
    cur = conn.execute(
        "UPDATE entity_salience_cache "
        "SET slow_state_hash = fast_state_hash, "
        "last_surprise = 0.0, "
        "boot_section_cache = 'one_line' "
        "WHERE fast_state_hash IS NOT NULL"
    )
    conn.commit()
    return cur.rowcount


@router.get("/boot-sections")
def get_boot_sections(
    persona: str = Query("web", description="Salience weight profile"),
    agent: str = Query("web", description="Agent for contextual scoring"),
    session_id: str | None = Query(
        None, description="Session ID for contextual scoping"
    ),
    max_full: int = Query(5, ge=1, le=20, description="Max full_section entities"),
    max_oneline: int = Query(15, ge=1, le=50, description="Max one_line entities"),
    type_exclude: str | None = Query(
        None, description="Comma-separated entity types to exclude"
    ),
) -> dict[str, Any]:
    """Salience-driven entity sections for boot briefings.

    Computes salience, applies cold-start caps, renders section Markdown,
    and advances slow state for all entities (boot = observation point).
    """
    t_now = datetime.now(UTC)
    excluded_types: set[str] = set()
    if type_exclude:
        excluded_types = {t.strip() for t in type_exclude.split(",") if t.strip()}

    conn = cortex_conn()
    try:
        results, hits, misses = compute_all_salience(
            conn,
            persona=persona,
            t_now=t_now,
            agent=agent,
            session_id=session_id,
        )

        if excluded_types:
            results = [r for r in results if r.entity_type not in excluded_types]

        full_results: list[SalienceResult] = []
        oneline_results: list[SalienceResult] = []
        for r in results:
            if r.boot_treatment == "full_section" and len(full_results) < max_full:
                full_results.append(r)
            elif len(oneline_results) < max_oneline:
                oneline_results.append(r)

        full_sections = _build_full_sections(conn, full_results, t_now)
        oneline_sections = _build_oneline_sections(conn, oneline_results, t_now)

        advanced = _advance_slow_state(conn)
        if advanced:
            logger.info("Boot slow state advanced for %d entities", advanced)
    finally:
        conn.close()

    return {
        "persona": persona,
        "computed_at": t_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entity_count": hits + misses,
        "sections": {
            "full": full_sections,
            "oneline": oneline_sections,
        },
        "cache_stats": {
            "full_count": len(full_sections),
            "oneline_count": len(oneline_sections),
            "total_scored": hits + misses,
            "slow_state_advanced": advanced,
        },
    }


_TEMPORAL_ACTIVE_SQL = """
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim,
           a.valid_from, a.valid_until, a.confidence
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE a.valid_until IS NOT NULL
      AND a.valid_until >= datetime('now')
      AND (a.valid_from IS NULL OR a.valid_from <= datetime('now'))
      AND a.review_status = 'committed'
      AND a.superseded_by IS NULL
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
    ORDER BY a.valid_from ASC
    LIMIT ?
"""


@router.get("/boot-temporal")
def get_boot_temporal(
    active_limit: int = Query(10, ge=1, le=50, description="Max active assertions"),
    upcoming_limit: int = Query(10, ge=1, le=50, description="Max upcoming assertions"),
) -> dict[str, Any]:
    """Temporally active and upcoming assertions for boot briefings.

    Active: assertions whose validity window includes now (valid_until in
    the future, valid_from in the past or null). Ordered by soonest expiry.

    Upcoming: assertions with valid_from in the next 7 days that haven't
    started yet. Ordered by soonest start.

    Only surfaces assertions with explicit temporal bounds — unbounded
    (valid_until IS NULL) assertions are excluded since they don't expire.
    """
    conn = cortex_conn()
    try:
        active_rows = db_query(conn, _TEMPORAL_ACTIVE_SQL, (active_limit,))
        upcoming_rows = db_query(conn, _TEMPORAL_UPCOMING_SQL, (upcoming_limit,))
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

    return {
        "active": [_format_row(r) for r in active_rows],
        "upcoming": [_format_row(r) for r in upcoming_rows],
    }


_BOOT_TODOS_SQL = """
    SELECT e.id, e.name,
           json_extract(e.attributes, '$.priority') as priority,
           json_extract(e.attributes, '$.domain') as domain,
           json_extract(e.attributes, '$.context') as context,
           e.description, e.source_uri
    FROM entities e
    WHERE e.type = 'todo'
    AND json_extract(e.attributes, '$.status') = 'open'
    {context_filter}
    ORDER BY
        CASE json_extract(e.attributes, '$.priority')
            WHEN 'high' THEN 1
            WHEN 'medium' THEN 2
            ELSE 3
        END,
        e.updated_at DESC
    LIMIT ?
"""


@router.get("/boot-todos")
def get_boot_todos(
    limit: int = Query(15, ge=1, le=50, description="Max open todos"),
    context: str | None = Query(
        None, description="Filter by context (e.g. 'code'). None = all."
    ),
) -> dict[str, Any]:
    """Open todo entities for boot briefings, priority-ordered.

    Cursor boot passes context=code to exclude personal/financial/legal todos.
    Web boot passes no context to see everything.
    """
    params: list[str | int] = []
    if context:
        context_filter = "AND json_extract(e.attributes, '$.context') = ?"
        params.append(context)
    else:
        context_filter = ""
    params.append(limit)

    sql = _BOOT_TODOS_SQL.format(context_filter=context_filter)
    conn = cortex_conn()
    try:
        rows = db_query(conn, sql, tuple(params))
    finally:
        conn.close()
    items = [
        {
            "id": r["id"],
            "title": r["name"],
            "priority": r["priority"],
            "domain": r["domain"],
            "context": r["context"],
            "description": r["description"],
            "source_uri": r["source_uri"],
        }
        for r in rows
    ]
    return {"items": items}


_COMMITMENTS_SQL = """
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim,
           a.confidence, a.valid_from, a.valid_until,
           a.resolution_status, a.created_at
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE a.resolution_status = 'pending'
      AND a.superseded_by IS NULL
      AND (a.valid_until IS NULL OR a.valid_until > datetime('now'))
    ORDER BY a.valid_from ASC
    LIMIT ?
"""

_LEGAL_CONTACTS_SQL = """
    SELECT DISTINCT se.from_node AS connected_id
    FROM session_edges se
    WHERE se.to_node LIKE 'legal_matter:%'
      AND se.valid_until IS NULL
      AND se.edge_type NOT IN ('supersedes', 'superseded_by')
    UNION
    SELECT DISTINCT se.to_node AS connected_id
    FROM session_edges se
    WHERE se.from_node LIKE 'legal_matter:%'
      AND se.valid_until IS NULL
      AND se.edge_type NOT IN ('supersedes', 'superseded_by')
"""

_ENTITY_RECENT_ASSERTIONS_SQL = """
    SELECT a.entity_id, a.claim, a.confidence, a.observed_at, a.created_at
    FROM assertions a
    WHERE a.entity_id = ?
      AND a.superseded_by IS NULL
    ORDER BY a.created_at DESC
    LIMIT 3
"""


@router.get("/boot-commitments")
def get_boot_commitments(
    limit: int = Query(10, ge=1, le=50, description="Max open commitments"),
) -> dict[str, Any]:
    """Open commitments (resolution_status='pending') for boot briefings.

    Returns count + top N by age (oldest first). Used by cortex_boot
    to surface unresolved promises alongside open investigations.
    """
    conn = cortex_conn()
    try:
        rows = db_query(conn, _COMMITMENTS_SQL, (limit,))
    finally:
        conn.close()

    items = [
        {
            "id": r["id"],
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "claim": r["claim"],
            "confidence": r["confidence"],
            "valid_from": r["valid_from"],
            "valid_until": r["valid_until"],
            "resolution_status": r["resolution_status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return {"count": len(items), "items": items}


@router.get("/boot-legal-contacts")
def get_boot_legal_contacts() -> dict[str, Any]:
    """Entities connected to active legal_matter entities via reasoning edges.

    For each connected entity, returns top 3 assertions by recency.
    Used by cortex_boot to expand visibility of legal matter participants.
    """
    conn = cortex_conn()
    try:
        connected_rows = db_query(conn, _LEGAL_CONTACTS_SQL)
        connected_ids = {
            r["connected_id"]
            for r in connected_rows
            if not r["connected_id"].startswith("legal_matter:")
            and not r["connected_id"].startswith("assertion:")
        }

        contacts: list[dict[str, Any]] = []
        for entity_id in sorted(connected_ids):
            entity_rows = db_query(
                conn,
                "SELECT id, name, type FROM entities WHERE id = ?",
                (entity_id,),
            )
            if not entity_rows:
                continue

            entity = entity_rows[0]
            assertion_rows = db_query(conn, _ENTITY_RECENT_ASSERTIONS_SQL, (entity_id,))
            contacts.append(
                {
                    "entity_id": entity_id,
                    "entity_name": entity["name"],
                    "entity_type": entity["type"],
                    "assertions": [
                        {
                            "claim": a["claim"],
                            "confidence": a["confidence"],
                            "observed_at": a.get("observed_at"),
                            "created_at": a["created_at"],
                        }
                        for a in assertion_rows
                    ],
                }
            )
    finally:
        conn.close()

    return {"count": len(contacts), "contacts": contacts}
