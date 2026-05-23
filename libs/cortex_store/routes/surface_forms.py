from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from universal_logging import get_logger

from ..db import cortex_conn, query
from ..models import (
    SurfaceFormCacheResult,
    SurfaceFormCreate,
    SurfaceFormItem,
    SurfaceFormList,
)

logger = get_logger("cortex-api.surface_forms")
router = APIRouter(prefix="/surface-forms", tags=["surface-forms"])

# Common columns used for SELECT statements to ensure consistency across queries.
_COLS = (
    "id, mention, entity_id, chunk_id, resolution_confidence, "
    "resolution_reasoning, context_hash, mention_type, created_at"
)


@router.get("", response_model=SurfaceFormList)
def list_surface_forms(
    mention: str | None = None,
    entity_id: str | None = None,
    chunk_id: int | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> SurfaceFormList:
    """List surface forms with optional mention/entity/chunk filters."""
    clauses: list[str] = []
    params: list[str | int] = []

    if mention:
        clauses.append("mention = ?")
        params.append(mention)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    if chunk_id is not None:
        clauses.append("chunk_id = ?")
        params.append(chunk_id)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {_COLS} FROM surface_forms{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    return SurfaceFormList(items=[SurfaceFormItem(**row) for row in rows])


@router.get("/cache", response_model=SurfaceFormCacheResult)
def cache_lookup(
    mention: str = Query(..., description="The surface form to look up"),
    context_hash: str = Query(
        ..., description="SHA-256 of lowercase(mention) + context"
    ),
) -> SurfaceFormCacheResult:
    """Cache lookup: mention + context_hash → entity_id.

    Returns hit=True with entity_id if a cached resolution exists,
    or hit=False if this mention+context has not been seen before.
    """
    with cortex_conn() as conn:
        rows = query(
            conn,
            "SELECT entity_id, resolution_confidence, resolution_reasoning "
            "FROM surface_forms "
            "WHERE mention = ? AND context_hash = ? "
            "LIMIT 1",
            (mention, context_hash),
        )

    if not rows:
        return SurfaceFormCacheResult(hit=False)
    return SurfaceFormCacheResult(
        hit=True,
        entity_id=rows[0]["entity_id"],
        resolution_confidence=rows[0]["resolution_confidence"],
        resolution_reasoning=rows[0]["resolution_reasoning"],
    )


@router.post("", response_model=SurfaceFormItem, status_code=status.HTTP_201_CREATED)
def create_surface_form(body: SurfaceFormCreate) -> SurfaceFormItem:
    """Create a surface form (entity mention resolution record)."""
    with cortex_conn() as conn:
        entities = query(
            conn, "SELECT id FROM entities WHERE id = ?", (body.entity_id,)
        )
        if not entities:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"Entity not found: {body.entity_id}"
            )

        cur = conn.execute(
            "INSERT INTO surface_forms "
            "(mention, entity_id, chunk_id, resolution_confidence, "
            " resolution_reasoning, context_hash, mention_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                body.mention,
                body.entity_id,
                body.chunk_id,
                body.resolution_confidence,
                body.resolution_reasoning,
                body.context_hash,
                body.mention_type,
            ),
        )
        conn.commit()
        rows = query(
            conn, f"SELECT {_COLS} FROM surface_forms WHERE id = ?", (cur.lastrowid,)
        )

    if not rows:
        logger.error("Surface form create succeeded but no row returned")
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Surface form created but could not be read back",
        )
    return SurfaceFormItem(**rows[0])


def _list_surface_forms_impl(**kwargs: object) -> dict[str, object]:
    return list_surface_forms(**kwargs).model_dump(mode="json")
