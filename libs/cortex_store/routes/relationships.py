from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, HTTPException, Query, status

from ..db import cortex_conn, query
from ..models import RelationshipCreate, RelationshipItem, RelationshipList

logger = logging.getLogger("cortex-api.relationships")
router = APIRouter(prefix="/relationships", tags=["relationships"])

_SELECT = """
    r.id, r.from_entity AS source_id, r.to_entity AS target_id,
    r.type AS type_id, rt.description AS type_name,
    se.name AS source_name, te.name AS target_name,
    r.role, r.strength, r.evidence, r.chunk_id,
    r.valid_from, r.valid_until, r.source_uri,
    r.created_at
"""

_FROM = """
    FROM relationships r
    JOIN relationship_types rt ON rt.type = r.type
    LEFT JOIN entities se ON se.id = r.from_entity
    LEFT JOIN entities te ON te.id = r.to_entity
"""


@router.get("", response_model=RelationshipList)
def list_relationships(
    entity_id: str | None = None,
    type_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> RelationshipList:
    """List relationships, optionally filtered by entity (source or target) or type."""
    clauses: list[str] = []
    params: list[str | int] = []

    if entity_id:
        clauses.append("(r.from_entity = ? OR r.to_entity = ?)")
        params.extend([entity_id, entity_id])
    if type_id:
        clauses.append("r.type = ?")
        params.append(type_id)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {_SELECT} {_FROM}{where} ORDER BY r.created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    return RelationshipList(items=[RelationshipItem(**row) for row in rows])


@router.post("", response_model=RelationshipItem, status_code=status.HTTP_201_CREATED)
def create_relationship(body: RelationshipCreate) -> RelationshipItem:
    """Create a typed relationship between two entities."""
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with cortex_conn() as conn:
        for eid, label in [
            (body.source_id, "source"),
            (body.target_id, "target"),
        ]:
            if not query(conn, "SELECT id FROM entities WHERE id = ?", (eid,)):
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    f"{label.title()} entity not found: {eid}",
                )

        if not query(
            conn,
            "SELECT type FROM relationship_types WHERE type = ?",
            (body.type_id,),
        ):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Relationship type not found: {body.type_id}",
            )

        cur = conn.execute(
            "INSERT INTO relationships "
            "(type, from_entity, to_entity, role, strength, evidence, "
            " chunk_id, valid_from, valid_until, source_uri, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.type_id,
                body.source_id,
                body.target_id,
                body.role,
                body.strength if body.strength is not None else 1.0,
                body.evidence,
                body.chunk_id,
                body.valid_from,
                body.valid_until,
                body.source_uri,
                now,
                now,
            ),
        )
        conn.commit()

        rows = query(
            conn,
            f"SELECT {_SELECT} {_FROM} WHERE r.id = ?",
            (cur.lastrowid,),
        )

    if not rows:
        logger.error("Relationship create succeeded but no row returned")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Relationship created but could not be read back",
        )
    return RelationshipItem(**rows[0])
