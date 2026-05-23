"""Tag assignments — Kumiho mutable tag pointers (Phase A2).

Named mutable references that point at specific assertions within an entity.
Enables point-in-time belief reconstruction and named states beyond "current"
(e.g. approved, initial, disputed, v1).

Kumiho Definition 4.2: tags are independent mutable references within an item.
UNIQUE(tag_name, entity_id) enforces one tag per entity per name — moving a tag
is an upsert.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from universal_logging import get_logger

from ..db import cortex_conn, execute, query

logger = get_logger("cortex-api.tags")
router = APIRouter(prefix="/tags", tags=["tags"])


class TagAssignRequest(BaseModel):
    tag_name: str
    entity_id: str
    assertion_id: int
    assigned_by: str


@router.get("")
def list_tags(
    entity_id: str = Query(..., description="Entity to list tags for"),
) -> dict[str, Any]:
    """List all tag assignments for an entity."""
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT id, tag_name, entity_id, assertion_id, assigned_at, assigned_by "
            "FROM tag_assignments WHERE entity_id = ? ORDER BY tag_name",
            (entity_id,),
        )
    return {"items": rows}


@router.put("")
def assign_tag(req: TagAssignRequest) -> dict[str, Any]:
    """Assign or move a tag pointer.  Upsert: existing tag_name+entity_id → move."""
    with cortex_conn() as conn:
        entity_rows = query(
            conn, "SELECT id FROM entities WHERE id = ?", (req.entity_id,)
        )
        if not entity_rows:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Entity not found: {req.entity_id}",
            )

        assertion_rows = query(
            conn,
            "SELECT id, entity_id FROM assertions WHERE id = ?",
            (req.assertion_id,),
        )
        if not assertion_rows:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Assertion not found: {req.assertion_id}",
            )
        if assertion_rows[0]["entity_id"] != req.entity_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Assertion {req.assertion_id} belongs to "
                    f"{assertion_rows[0]['entity_id']}, not {req.entity_id}"
                ),
            )

        conn.execute(
            "INSERT INTO tag_assignments (tag_name, entity_id, assertion_id, assigned_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(tag_name, entity_id) DO UPDATE SET "
            "assertion_id = excluded.assertion_id, "
            "assigned_at = datetime('now'), "
            "assigned_by = excluded.assigned_by",
            (req.tag_name, req.entity_id, req.assertion_id, req.assigned_by),
        )
        conn.commit()

        rows = query(
            conn,
            "SELECT id, tag_name, entity_id, assertion_id, assigned_at, assigned_by "
            "FROM tag_assignments WHERE tag_name = ? AND entity_id = ?",
            (req.tag_name, req.entity_id),
        )

    logger.info(
        "Tag assigned: %s → assertion %d on %s (by %s)",
        req.tag_name,
        req.assertion_id,
        req.entity_id,
        req.assigned_by,
    )
    return rows[0] if rows else {"ok": True}


@router.delete("/{tag_name}")
def delete_tag(
    tag_name: str,
    entity_id: str = Query(..., description="Entity to remove tag from"),
) -> None:
    """Remove a tag assignment.  Returns 204 on success."""
    with cortex_conn() as conn:
        affected = execute(
            conn,
            "DELETE FROM tag_assignments WHERE tag_name = ? AND entity_id = ?",
            (tag_name, entity_id),
        )
    if affected == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag {tag_name!r} not found for entity {entity_id}",
        )
    logger.info("Tag deleted: %s on %s", tag_name, entity_id)


def _list_tags_impl(*, entity_id: str) -> dict[str, Any]:
    return list_tags(entity_id=entity_id)


def _assign_tag_impl(payload: dict[str, Any]) -> dict[str, Any]:
    return assign_tag(TagAssignRequest.model_validate(payload))
