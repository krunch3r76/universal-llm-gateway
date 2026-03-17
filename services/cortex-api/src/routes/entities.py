from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, HTTPException, Query, status

from src.db import cortex_conn, decode_row, json_encode, query
from src.models import (
    AssertionItem,
    EntityCreate,
    EntityDetail,
    EntityList,
    EntitySummary,
)

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
    sql = f"SELECT id, type, name, created_at FROM entities{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = cortex_conn()
    try:
        rows = query(conn, sql, tuple(params))
    finally:
        conn.close()

    return EntityList(items=[EntitySummary(**row) for row in rows])


_ENTITY_JSON_FIELDS = frozenset({"aliases", "attributes"})
_ASSERTION_JSON_FIELDS = frozenset({"evidence_uris"})


@router.get("/{entity_id}", response_model=EntityDetail)
def get_entity(entity_id: str) -> EntityDetail:
    """Fetch one entity and include all linked assertions ordered by newest first."""
    conn = cortex_conn()
    try:
        entities = query(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,))
        if not entities:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity not found: {entity_id}",
            )
        entity = entities[0]

        assertion_rows = query(
            conn,
            "SELECT id, entity_id, claim, confidence, evidence, "
            "evidence_uris, created_at FROM assertions WHERE entity_id = ? "
            "ORDER BY created_at DESC",
            (entity_id,),
        )
    finally:
        conn.close()

    assertions = [
        AssertionItem(**decode_row(row, _ASSERTION_JSON_FIELDS))
        for row in assertion_rows
    ]
    return EntityDetail(
        **decode_row(entity, _ENTITY_JSON_FIELDS), assertions=assertions
    )


@router.post("", response_model=EntityDetail, status_code=status.HTTP_201_CREATED)
def create_entity(body: EntityCreate) -> EntityDetail:
    """Create an entity and return the stored entity detail payload."""
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = cortex_conn()
    try:
        conn.execute(
            "INSERT INTO entities (id, type, name, aliases, attributes, "
            "notes, source_uri, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.id,
                body.type,
                body.name,
                json_encode(body.aliases),
                json_encode(body.attributes),
                body.notes,
                body.source_uri,
                now,
                now,
            ),
        )
        conn.commit()
        rows = query(conn, "SELECT * FROM entities WHERE id = ?", (body.id,))
    except conn.IntegrityError:
        logger.warning("Entity create conflict for id=%s", body.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Entity already exists: {body.id}",
        )
    finally:
        conn.close()

    if not rows:
        logger.error("Entity create succeeded but no row returned for id=%s", body.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Entity created but could not be read back",
        )
    return EntityDetail(**decode_row(rows[0], _ENTITY_JSON_FIELDS), assertions=[])
