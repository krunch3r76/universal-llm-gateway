from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from ..db import cortex_conn, query
from ..models import ChunkCreate, ChunkItem, ChunkList

logger = logging.getLogger("cortex-api.chunks")
router = APIRouter(prefix="/chunks", tags=["chunks"])

_COLS = (
    "id, content, source_uri, source_date, observer, chunk_index, "
    "extraction_run, token_count, created_at"
)


@router.get("", response_model=ChunkList)
def list_chunks(
    source_uri: str | None = None,
    source_date_from: str | None = None,
    source_date_to: str | None = None,
    observer: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> ChunkList:
    """List chunks with optional source/date/observer filters."""
    query_conditions = []
    query_params = []

    if source_uri:
        query_conditions.append("source_uri = ?")
        query_params.append(source_uri)
    if source_date_from:
        query_conditions.append("source_date >= ?")
        query_params.append(source_date_from)
    if source_date_to:
        query_conditions.append("source_date <= ?")
        query_params.append(source_date_to)
    if observer:
        query_conditions.append("observer = ?")
        query_params.append(observer)

    where_clause = (
        f" WHERE {' AND '.join(query_conditions)}" if query_conditions else ""
    )
    sql = f"SELECT {_COLS} FROM chunks{where_clause} ORDER BY created_at DESC LIMIT ?"
    query_params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(query_params))

    return ChunkList(items=[ChunkItem(**row) for row in rows])


@router.get("/{chunk_id}", response_model=ChunkItem)
def get_chunk(chunk_id: int) -> ChunkItem:
    """Get a single chunk with content."""
    with cortex_conn() as conn:
        rows = query(conn, f"SELECT {_COLS} FROM chunks WHERE id = ?", (chunk_id,))

    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Chunk not found: {chunk_id}")
    return ChunkItem(**rows[0])


@router.post("", response_model=ChunkItem, status_code=status.HTTP_201_CREATED)
def create_chunk(body: ChunkCreate) -> ChunkItem:
    """Create a chunk and return the persisted row."""
    with cortex_conn() as conn:
        cur = conn.execute(
            "INSERT INTO chunks "
            "(content, source_uri, source_date, observer, chunk_index, "
            " extraction_run, token_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                body.content,
                body.source_uri,
                body.source_date,
                body.observer,
                body.chunk_index,
                body.extraction_run,
                body.token_count,
            ),
        )
        conn.commit()
        rows = query(conn, f"SELECT {_COLS} FROM chunks WHERE id = ?", (cur.lastrowid,))

    if not rows:
        # Consider logging the specific exception if query failed, or if cur.lastrowid was invalid
        logger.error(
            "Chunk create succeeded but no row returned, possibly due to a subsequent read error or invalid lastrowid."
        )
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Chunk created but could not be read back",
        )
    return ChunkItem(**rows[0])
