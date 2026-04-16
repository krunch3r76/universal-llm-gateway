from __future__ import annotations

import datetime
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request, status

from ..action_hints import detect_expired_unresolved
from ..db import cortex_conn, decode_row, execute, json_encode, query
from ..models import (
    AssertionItem,
    EdgeItem,
    EntityCreate,
    EntityDetail,
    EntityList,
    EntitySummary,
    EntityUpdate,
    RelationshipItem,
)
from .assertions import _ASSERTION_COLS
from .edges import _EDGE_COLS

logger = logging.getLogger("cortex-api.entities")
router = APIRouter(prefix="/entities", tags=["entities"])


@router.get("", response_model=EntityList)
def list_entities(
    type: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> EntityList:
    """List entities, optionally constrained to one entity type."""
    clauses: list[str] = []
    params: list[str | int] = []

    if type:
        clauses.append("type = ?")
        params.append(type)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT id, type, name, description, status, content_hash, created_at FROM entities{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = None
    try:
        conn = cortex_conn()
        rows = query(conn, sql, tuple(params))
    finally:
        if conn:
            conn.close()

    return EntityList(items=[EntitySummary(**row) for row in rows])


_ENTITY_JSON_FIELDS = frozenset({"aliases", "attributes"})
_ASSERTION_JSON_FIELDS = frozenset({"evidence_uris"})

_RELATIONSHIP_SELECT = """
    r.id, r.from_entity AS source_id, r.to_entity AS target_id,
    r.type AS type_id, rt.description AS type_name,
    se.name AS source_name, te.name AS target_name,
    r.role, r.strength, r.evidence, r.chunk_id,
    r.valid_from, r.valid_until, r.source_uri,
    r.session_id, r.agent, r.created_at
"""

_RELATIONSHIP_FROM = """
    FROM relationships r
    JOIN relationship_types rt ON rt.type = r.type
    LEFT JOIN entities se ON se.id = r.from_entity
    LEFT JOIN entities te ON te.id = r.to_entity
"""


@router.get("/{entity_id}", response_model=EntityDetail)
def get_entity(
    entity_id: str,
    request: Request,
    include_edges: bool = Query(
        False, description="Include reasoning edges from session_edges"
    ),
    edge_limit: int = Query(
        20, ge=1, le=100, description="Max reasoning edges to return"
    ),
) -> EntityDetail:
    """Fetch one entity with linked assertions, relationships, and optionally reasoning edges."""
    source = request.headers.get("x-cortex-source", "agent")
    agent = request.headers.get("x-cortex-agent", "web")
    session_id = request.headers.get("x-cortex-session")

    conn = None
    try:
        conn = cortex_conn()
        entities = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {entity_id}",
            )
        entity = entities[0]

        assertion_rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE entity_id = ? "
            "ORDER BY created_at DESC",
            (entity_id,),
        )

        rel_rows = query(
            conn,
            f"SELECT {_RELATIONSHIP_SELECT} {_RELATIONSHIP_FROM} "
            "WHERE (r.from_entity = ? OR r.to_entity = ?) AND r.active = 1 "
            "ORDER BY r.created_at DESC",
            (entity_id, entity_id),
        )

        edge_rows: list[dict] = []
        if include_edges:
            edge_rows = query(
                conn,
                f"SELECT {_EDGE_COLS} FROM session_edges "
                "WHERE (from_node = ? OR to_node = ?) "
                "AND valid_until IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (entity_id, entity_id, edge_limit),
            )

        if source != "boot":
            try:
                conn.execute(
                    "INSERT INTO entity_access_log "
                    "(entity_id, agent, operation, source, session_id) "
                    "VALUES (?, ?, 'entity_get', ?, ?)",
                    (entity_id, agent, source, session_id),
                )
                conn.commit()
            except Exception:
                logger.debug("Access log insert failed for %s", entity_id)
    finally:
        if conn:
            conn.close()

    assertions: list[AssertionItem] = []
    for row in assertion_rows:
        try:
            assertions.append(AssertionItem(**decode_row(row, _ASSERTION_JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s for entity %s — deserialization failed",
                row.get("id"),
                entity_id,
                exc_info=True,
            )
    relationships = [RelationshipItem(**row) for row in rel_rows]
    edges = [EdgeItem(**row) for row in edge_rows]
    hints = detect_expired_unresolved([a.model_dump() for a in assertions])
    return EntityDetail(
        **decode_row(entity, _ENTITY_JSON_FIELDS),
        assertions=assertions,
        relationships=relationships,
        reasoning_edges=edges,
        action_hints=hints or None,
    )


_JSON_COLUMNS = frozenset({"aliases", "attributes"})


@router.patch("/{entity_id}", response_model=EntityDetail)
def update_entity(entity_id: str, body: EntityUpdate) -> EntityDetail:
    """Update mutable fields on an entity.

    Uses ``model_fields_set`` so omitted keys are untouched while explicitly
    sending ``null`` clears the field (sets it to SQL NULL).
    """
    conn = None
    try:
        conn = cortex_conn()
        existing = query(conn, "SELECT id FROM entities WHERE id = ?", (entity_id,))
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {entity_id}",
            )

        sets: list[str] = []
        params: list[object] = []
        for field in body.model_fields_set:
            value = getattr(body, field)
            if field in _JSON_COLUMNS:
                value = json_encode(value)
            sets.append(f"{field} = ?")
            params.append(value)

        if not sets:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="No updatable fields provided",
            )

        now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        sets.append("updated_at = ?")
        params.append(now)
        params.append(entity_id)

        execute(
            conn, f"UPDATE entities SET {', '.join(sets)} WHERE id = ?", tuple(params)
        )

        rows = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
        assertion_rows = query(
            conn,
            f"SELECT {_ASSERTION_COLS} FROM assertions WHERE entity_id = ? "
            "ORDER BY created_at DESC",
            (entity_id,),
        )
    finally:
        if conn:
            conn.close()

    assertions: list[AssertionItem] = []
    for row in assertion_rows:
        try:
            assertions.append(AssertionItem(**decode_row(row, _ASSERTION_JSON_FIELDS)))
        except Exception:
            logger.error(
                "Skipping assertion %s for entity %s — deserialization failed",
                row.get("id"),
                entity_id,
                exc_info=True,
            )
    return EntityDetail(
        **decode_row(rows[0], _ENTITY_JSON_FIELDS), assertions=assertions
    )


@router.post("", response_model=EntityDetail, status_code=status.HTTP_201_CREATED)
def create_entity(body: EntityCreate) -> EntityDetail:
    """Create an entity and return the stored entity detail payload."""
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = None
    try:
        conn = cortex_conn()
        conn.execute(
            "INSERT INTO entities (id, type, name, description, status, aliases, "
            "attributes, notes, source_uri, content_hash, "
            "retention_policy, retention_ttl_days, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.id,
                body.type,
                body.name,
                body.description,
                body.status or "confirmed",
                json_encode(body.aliases),
                json_encode(body.attributes),
                body.notes,
                body.source_uri,
                body.content_hash,
                body.retention_policy or "permanent",
                body.retention_ttl_days,
                now,
                now,
            ),
        )
        conn.commit()
        rows = query(conn, "SELECT * FROM entities WHERE id = ?", (body.id,))
    except sqlite3.IntegrityError:  # Assuming sqlite3 is the underlying DB
        logger.warning("Entity create conflict for id=%s", body.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Entity already exists: {body.id}",
        )
    finally:
        if conn:
            conn.close()

    if not rows:
        logger.error("Entity create succeeded but no row returned for id=%s", body.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Entity created but could not be read back",
        )
    return EntityDetail(**decode_row(rows[0], _ENTITY_JSON_FIELDS), assertions=[])
