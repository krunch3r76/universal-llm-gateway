"""GET /boot-sections — salience-driven entity sections for boot briefings."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query

from ..action_hints import detect_expired_unresolved
from ..db import cortex_conn
from ..db import query as db_query
from ..salience import SalienceResult, compute_all_salience

logger = logging.getLogger("cortex-api.boot")
router = APIRouter(tags=["boot"])


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    rows = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return rows is not None


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
    tags: list[dict[str, Any]] | None = None,
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

    non_current_tags = [t for t in (tags or []) if t["tag_name"] != "current"]
    if non_current_tags:
        tag_parts = [
            f"{t['tag_name']} (assertion #{t['assertion_id']})"
            for t in non_current_tags
        ]
        lines.append(f"**Tags**: {', '.join(tag_parts)}")
        lines.append("")

    for a in assertions:
        conf = a.get("confidence", "?")
        ent_score = a.get("entrenchment_score")
        score_suffix = f" (e={ent_score:.2f})" if ent_score else ""
        lines.append(f"- [{conf}]{score_suffix} {a.get('claim', '')}")
        prospective = a.get("prospective_summary")
        if prospective:
            lines.append(f"  *Prospective*: {prospective}")

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


def _resolve_current_assertion_id(
    conn: sqlite3.Connection, entity_id: str
) -> int | None:
    """Check for a 'current' tag; return its assertion_id or None to fall back."""
    rows = db_query(
        conn,
        "SELECT assertion_id FROM tag_assignments "
        "WHERE tag_name = 'current' AND entity_id = ?",
        (entity_id,),
    )
    return rows[0]["assertion_id"] if rows else None


def _build_full_sections(
    conn: sqlite3.Connection,
    results: list[SalienceResult],
    now: datetime,
) -> list[dict[str, Any]]:
    """Fetch assertions/relationships and render full sections."""
    _has_tags_table = _table_exists(conn, "tag_assignments")

    sections: list[dict[str, Any]] = []
    for r in results:
        current_aid: int | None = None
        if _has_tags_table:
            current_aid = _resolve_current_assertion_id(conn, r.entity_id)

        if current_aid is not None:
            assertions = db_query(
                conn,
                "SELECT entity_id, claim, confidence, evidence, "
                "prospective_summary, entrenchment_score, observed_at, created_at "
                "FROM assertions WHERE id = ?",
                (current_aid,),
            )
        else:
            assertions = db_query(
                conn,
                "SELECT entity_id, claim, confidence, evidence, "
                "prospective_summary, entrenchment_score, observed_at, created_at "
                "FROM assertions "
                "WHERE entity_id = ? AND superseded_by IS NULL "
                "ORDER BY COALESCE(entrenchment_score, 0.0) DESC, created_at DESC "
                "LIMIT 5",
                (r.entity_id,),
            )

        entity_tags: list[dict[str, Any]] = []
        if _has_tags_table:
            entity_tags = db_query(
                conn,
                "SELECT tag_name, assertion_id FROM tag_assignments "
                "WHERE entity_id = ? ORDER BY tag_name",
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
                "section_markdown": _render_full_section(
                    r, assertions, rel_rows, now, tags=entity_tags
                ),
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
      AND (a.resolution_status IS NULL OR a.resolution_status = 'pending')
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

# Assertions that had a future valid_until within the last 30 days but are now
# superseded. These represent recently-resolved temporal matters — used by cortex_boot
# to suppress stale open_items in session journals that still reference the matter
# as pending.
_TEMPORAL_RECENTLY_RESOLVED_SQL = """
    SELECT a.id, a.entity_id, e.name AS entity_name, a.claim,
           a.valid_from, a.valid_until, a.confidence
    FROM assertions a
    JOIN entities e ON a.entity_id = e.id
    WHERE a.valid_until IS NOT NULL
      AND a.valid_until >= datetime('now', '-30 days')
      AND a.superseded_by IS NOT NULL
      AND a.review_status = 'committed'
    ORDER BY a.valid_until DESC
    LIMIT ?
"""


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

    Recently resolved: temporal assertions (valid_until set) that were superseded
    within the last 30 days. Used by cortex_boot to tag stale open_items in session
    journals that still reference the matter as pending.

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
        resolved_rows = db_query(
            conn, _TEMPORAL_RECENTLY_RESOLVED_SQL, (resolved_limit,)
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
        "recently_resolved": [_format_row(r) for r in resolved_rows],
        "expired_unresolved": expired_formatted,
    }
    if hints:
        result["action_hints"] = [h.model_dump() for h in hints]
    return result


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
    {domain_filter}
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
    domain_exclude: str | None = Query(
        None,
        description="Comma-separated domains to exclude (e.g. 'infra,rag,pipeline').",
    ),
) -> dict[str, Any]:
    """Open todo entities for boot briefings, priority-ordered.

    Cursor boot passes context=code to exclude personal/financial/legal todos.
    Web boot passes domain_exclude to filter out infra/rag/pipeline/mcp todos.
    """
    params: list[str | int] = []
    if context:
        context_filter = "AND json_extract(e.attributes, '$.context') = ?"
        params.append(context)
    else:
        context_filter = ""

    domain_filter = ""
    if domain_exclude:
        excluded = [d.strip() for d in domain_exclude.split(",") if d.strip()]
        if excluded:
            placeholders = ",".join("?" * len(excluded))
            domain_filter = (
                f"AND (json_extract(e.attributes, '$.domain') IS NULL "
                f"OR json_extract(e.attributes, '$.domain') NOT IN ({placeholders}))"
            )
            params.extend(excluded)

    params.append(limit)

    sql = _BOOT_TODOS_SQL.format(
        context_filter=context_filter, domain_filter=domain_filter
    )
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


_BOOT_REFLECTIVE_SQL = """
    SELECT id, agent, register, entry, kind, session_id, created_at
    FROM reflective_journal
    WHERE agent = ?
    ORDER BY id DESC
    LIMIT ?
"""


# Recent mentions: entities with new assertions OR newly-created entities within
# trailing window. Surfaces names that came up in session work so boot agents
# recognize them without re-derivation. Noisy system types are excluded by
# default; callers may override via type_exclude.
_RECENT_MENTIONS_DEFAULT_EXCLUDE = ("transcript", "todo", "journal", "assertion")

_RECENT_MENTIONS_SQL = """
    SELECT
        e.id AS entity_id,
        e.name AS entity_name,
        e.type AS entity_type,
        e.created_at AS entity_created_at,
        COUNT(a.id) AS recent_mention_count,
        MAX(COALESCE(a.created_at, e.created_at)) AS last_mentioned_at
    FROM entities e
    LEFT JOIN assertions a
        ON a.entity_id = e.id
        AND a.created_at > datetime('now', ?)
        AND a.superseded_by IS NULL
    WHERE (
            e.created_at > datetime('now', ?)
            OR a.id IS NOT NULL
          )
      AND (e.status IS NULL OR e.status != 'deprecated')
      {type_filter}
    GROUP BY e.id
    ORDER BY last_mentioned_at DESC
    LIMIT ?
"""


@router.get("/boot-recent-mentions")
def get_boot_recent_mentions(
    days: int = Query(7, ge=1, le=30, description="Trailing window in days"),
    limit: int = Query(10, ge=1, le=30, description="Max entities"),
    type_exclude: str | None = Query(
        None,
        description=(
            "Comma-separated entity types to exclude. "
            "Defaults to 'transcript,todo,journal,assertion' "
            "(system/meta types already surfaced elsewhere)."
        ),
    ),
) -> dict[str, Any]:
    """Entities recently mentioned via new assertions or new entity creation.

    Surfaces a roster of names that came up in trailing session work so the
    boot agent recognizes them without re-derivation. Covers the case where
    Kaywan references a person/organization that was introduced in a prior
    session — the entity exists in the graph, but the boot card previously
    had no way to surface it unless it appeared in another section (deadlines,
    todos, etc.).

    Default window: 7 days. Default exclusions: transcript, todo, journal,
    assertion (already surfaced elsewhere or noisy).
    """
    if type_exclude is None:
        excluded = list(_RECENT_MENTIONS_DEFAULT_EXCLUDE)
    else:
        excluded = [t.strip() for t in type_exclude.split(",") if t.strip()]

    type_filter = ""
    params: list[Any] = [f"-{days} days", f"-{days} days"]
    if excluded:
        placeholders = ",".join("?" * len(excluded))
        type_filter = f"AND e.type NOT IN ({placeholders})"
        params.extend(excluded)
    params.append(limit)

    sql = _RECENT_MENTIONS_SQL.format(type_filter=type_filter)
    conn = cortex_conn()
    try:
        rows = db_query(conn, sql, tuple(params))
    finally:
        conn.close()

    items = [
        {
            "entity_id": r["entity_id"],
            "entity_name": r["entity_name"],
            "entity_type": r["entity_type"],
            "recent_mention_count": r["recent_mention_count"],
            "last_mentioned_at": r["last_mentioned_at"],
            "entity_created_at": r["entity_created_at"],
        }
        for r in rows
    ]
    return {"items": items, "window_days": days, "excluded_types": excluded}


@router.get("/boot-reflective")
def get_boot_reflective(
    agent: str = Query("web", description="Agent whose reflective entries to surface"),
    limit: int = Query(5, ge=1, le=20, description="Max entries"),
) -> dict[str, Any]:
    """Recent reflective journal entries for boot briefings.

    Surfaces the latest entries by the specified agent, ordered newest-first.
    Consolidation entries are included alongside raw entries so the boot
    can present both the living grain and any synthesized throughlines.
    """
    conn = cortex_conn()
    try:
        if not _table_exists(conn, "reflective_journal"):
            return {"items": [], "total": 0}
        rows = db_query(conn, _BOOT_REFLECTIVE_SQL, (agent, limit))
        total_row = db_query(
            conn,
            "SELECT COUNT(*) AS cnt FROM reflective_journal WHERE agent = ?",
            (agent,),
        )
        total = total_row[0]["cnt"] if total_row else 0
    finally:
        conn.close()

    items = [
        {
            "id": r["id"],
            "register": r["register"],
            "entry": r["entry"][:300],
            "kind": r["kind"],
            "session_id": r.get("session_id"),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return {"items": items, "total": total}
