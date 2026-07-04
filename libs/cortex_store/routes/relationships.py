"""Relationship ops — list, create, update, and soft-delete."""

from __future__ import annotations

import datetime
import difflib
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Response, status
from universal_logging import get_logger

from ..db import cortex_conn, query
from ..models import (
    RelationshipCreate,
    RelationshipCreateResponse,
    RelationshipDeleteResponse,
    RelationshipItem,
    RelationshipList,
    RelationshipUpdate,
)
from ..relationship_sql import FROM_CLAUSE, SELECT_COLUMNS, SYMMETRIC_REL_TYPES

logger = get_logger("cortex-api.relationships")
router = APIRouter(prefix="/relationships", tags=["relationships"])


def _relationship_type_not_found_message(conn: sqlite3.Connection, type_id: str) -> str:
    if query(
        conn,
        "SELECT 1 FROM session_edge_types WHERE type = ?",
        (type_id,),
    ):
        return (
            f"Relationship type not found: {type_id!r}. "
            f"{type_id!r} is an EDGE type (use edge_create); "
            "the relationship analogue is 'related_to'."
        )
    rel_types = [
        row["type"]
        for row in query(conn, "SELECT type FROM relationship_types ORDER BY type", ())
    ]
    suggestion = difflib.get_close_matches(type_id, rel_types, n=1)
    if suggestion:
        return (
            f"Relationship type not found: {type_id!r}. Did you mean {suggestion[0]!r}?"
        )
    return f"Relationship type not found: {type_id!r}."


@router.get("", response_model=RelationshipList)
def list_relationships(
    entity_id: str | None = None,
    type_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> RelationshipList:
    """List active relationships, optionally filtered by entity or type."""
    clauses: list[str] = ["r.active = 1"]
    params: list[str | int] = []

    if entity_id:
        clauses.append("(r.from_entity = ? OR r.to_entity = ?)")
        params.extend([entity_id, entity_id])
    if type_id:
        clauses.append("r.type = ?")
        params.append(type_id)

    where = f" WHERE {' AND '.join(clauses)}"
    sql = f"SELECT {SELECT_COLUMNS} {FROM_CLAUSE}{where} ORDER BY r.created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    return RelationshipList(items=[RelationshipItem(**row) for row in rows])


def create_relationship_on_conn(
    conn: sqlite3.Connection,
    body: RelationshipCreate,
    *,
    commit: bool = True,
    post_commit_emits: list | None = None,
) -> RelationshipCreateResponse:
    """Create a typed relationship on a caller-supplied connection.

    Extracted from the route handler so composites can run the entity
    existence checks, type check, symmetric canonicalization, dedup, and
    INSERT inside their own transaction — uncommitted entity rows are only
    visible on the same connection. ``commit=False`` leaves the write
    uncommitted; the caller owns the transaction boundary. ``post_commit_emits``
    follows the ``update_entity_impl`` deferred-emit idiom; this impl emits
    no events itself, so the list is accepted for signature parity and left
    untouched.

    Duplicate active relationships (same from/to/type) are silent no-ops
    that return the existing relationship with ``was_new: false``.
    """
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    from_entity = body.source_id
    to_entity = body.target_id
    if body.type_id in SYMMETRIC_REL_TYPES:
        from_entity, to_entity = (
            min(from_entity, to_entity),
            max(from_entity, to_entity),
        )

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
            _relationship_type_not_found_message(conn, body.type_id),
        )

    try:
        cur = conn.execute(
            "INSERT INTO relationships "
            "(type, from_entity, to_entity, role, strength, evidence, "
            " chunk_id, valid_from, valid_until, source_uri, "
            " session_id, agent, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.type_id,
                from_entity,
                to_entity,
                body.role,
                body.strength if body.strength is not None else 1.0,
                body.evidence,
                body.chunk_id,
                body.valid_from,
                body.valid_until,
                body.source_uri,
                body.session_id,
                body.agent,
                now,
                now,
            ),
        )
        if commit:
            conn.commit()
        was_new = True
        rows = query(
            conn,
            f"SELECT {SELECT_COLUMNS} {FROM_CLAUSE} WHERE r.id = ?",
            (cur.lastrowid,),
        )
    except sqlite3.IntegrityError:
        was_new = False
        rows = query(
            conn,
            f"SELECT {SELECT_COLUMNS} {FROM_CLAUSE} "
            "WHERE r.from_entity = ? AND r.to_entity = ? AND r.type = ? AND r.active = 1",
            (from_entity, to_entity, body.type_id),
        )
        if rows:
            logger.info(
                "Relationship dedup: existing %s -[%s]-> %s (id=%d)",
                from_entity,
                body.type_id,
                to_entity,
                rows[0]["id"],
            )

    if not rows:
        logger.error("Relationship create: no row found after insert/dedup")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Relationship created but could not be read back",
        )

    item = RelationshipItem(**rows[0])
    return RelationshipCreateResponse(was_new=was_new, item=item)


@router.post("", response_model=RelationshipCreateResponse)
def create_relationship(
    body: RelationshipCreate, response: Response
) -> RelationshipCreateResponse:
    """Create a typed relationship between two entities with idempotent dedup.

    Duplicate active relationships (same from/to/type) are silent no-ops
    that return the existing relationship with ``was_new: false``.
    Symmetric relationship types have their direction canonicalized.
    """
    with cortex_conn() as conn:
        result = create_relationship_on_conn(conn, body, commit=True)

    response.status_code = (
        status.HTTP_201_CREATED if result.was_new else status.HTTP_200_OK
    )
    return result


@router.delete("/{relationship_id}", response_model=RelationshipDeleteResponse)
def delete_relationship(relationship_id: int) -> RelationshipDeleteResponse:
    """Soft-delete a relationship by setting active=0.

    The row is preserved for provenance. Active relationships exclude deleted
    rows by default. To correct source, target, or type — which define
    relationship identity — delete and recreate with the correct values.
    """
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, active FROM relationships WHERE id = ?",
            (relationship_id,),
        )
        if not rows:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Relationship {relationship_id} not found",
            )
        if not rows[0]["active"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Relationship {relationship_id} is already deleted",
            )
        conn.execute(
            "UPDATE relationships SET active = 0, updated_at = ? WHERE id = ?",
            (now, relationship_id),
        )
        conn.commit()
    return RelationshipDeleteResponse(deleted=True, id=relationship_id)


@router.patch("/{relationship_id}", response_model=RelationshipItem)
def update_relationship(
    relationship_id: int,
    body: RelationshipUpdate,
) -> RelationshipItem:
    """Patch mutable fields of an active relationship.

    Only supplied non-null fields are updated. Source, target, and type define
    relationship identity and cannot be changed — delete and recreate instead.
    """
    now = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No fields to update",
        )
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, active FROM relationships WHERE id = ?",
            (relationship_id,),
        )
        if not rows:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"Relationship {relationship_id} not found",
            )
        if not rows[0]["active"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Relationship {relationship_id} is deleted",
            )
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params: list[object] = [*updates.values(), now, relationship_id]
        conn.execute(
            f"UPDATE relationships SET {set_clause}, updated_at = ? WHERE id = ?",
            params,
        )
        conn.commit()
        updated = query(
            conn,
            f"SELECT {SELECT_COLUMNS} {FROM_CLAUSE} WHERE r.id = ?",
            (relationship_id,),
        )
    if not updated:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Could not read back updated relationship",
        )
    return RelationshipItem(**updated[0])


def _list_relationships_impl(**kwargs: object) -> dict[str, object]:
    return list_relationships(**kwargs).model_dump(mode="json")


def _create_relationship_impl(payload: dict[str, object]) -> dict[str, object]:
    response = Response()
    data = create_relationship(RelationshipCreate.model_validate(payload), response)
    return data.model_dump(mode="json")


def _delete_relationship_impl(relationship_id: int) -> dict[str, object]:
    return delete_relationship(relationship_id).model_dump(mode="json")


def _update_relationship_impl(
    relationship_id: int, payload: dict[str, object]
) -> dict[str, object]:
    return update_relationship(
        relationship_id, RelationshipUpdate.model_validate(payload)
    ).model_dump(mode="json")
