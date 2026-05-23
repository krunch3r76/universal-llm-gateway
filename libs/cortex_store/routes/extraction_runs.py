"""Extraction runs — source gate for Cortex ingestion (Phase 4 dedup guard).

pre_ingest_check() is a performance optimization: skip extraction entirely when
the source content hasn't changed. Correctness does NOT depend on this gate —
idempotent write primitives (Phase 2) absorb re-ingestion safely.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query, Response, status
from universal_logging import get_logger

from ..db import cortex_conn, query
from ..models import (
    ExtractionCheckRequest,
    ExtractionCheckResponse,
    ExtractionRunComplete,
    ExtractionRunItem,
)

logger = get_logger("cortex-api.extraction-runs")
router = APIRouter(prefix="/extraction-runs", tags=["extraction-runs"])

_COLS = (
    "id, source_uri, content_hash, status, assertion_count, created_at, completed_at"
)


@router.post("/check", response_model=ExtractionCheckResponse)
def pre_ingest_check(
    body: ExtractionCheckRequest, response: Response
) -> ExtractionCheckResponse:
    """Check whether extraction should proceed for a source_uri + content_hash.

    Returns one of three actions:
    - ``proceed``: no prior run exists — a new run is registered.
    - ``skip``: prior run exists with identical content_hash — extraction
      would produce no-op writes.
    - ``re-extract``: prior run exists with different content_hash — old run's
      assertions are bulk-superseded and a new run is registered.
    """
    conn = cortex_conn()
    try:
        prior_rows = query(
            conn,
            "SELECT id, content_hash, assertion_count FROM extraction_runs "
            "WHERE source_uri = ? ORDER BY created_at DESC LIMIT 1",
            (body.source_uri,),
        )

        if not prior_rows:
            cur = conn.execute(
                "INSERT INTO extraction_runs (source_uri, content_hash, status) "
                "VALUES (?, ?, 'registered')",
                (body.source_uri, body.content_hash),
            )
            conn.commit()
            response.status_code = status.HTTP_201_CREATED
            return ExtractionCheckResponse(
                action="proceed",
                run_id=cur.lastrowid,  # type: ignore[arg-type]
            )

        prior = prior_rows[0]
        prior_id: int = prior["id"]
        prior_hash: str | None = prior["content_hash"]

        if prior_hash == body.content_hash:
            response.status_code = status.HTTP_200_OK
            logger.info(
                "Source gate SKIP: source_uri=%s content_hash unchanged (run %d)",
                body.source_uri,
                prior_id,
            )
            return ExtractionCheckResponse(action="skip", run_id=prior_id)

        # Different hash → re-extract: supersede prior run's active assertions
        supersede_cur = conn.execute(
            "UPDATE assertions "
            "SET superseded_by = -1, review_status = 'rejected' "
            "WHERE extraction_run = ? AND superseded_by IS NULL",
            (prior_id,),
        )
        superseded_count = supersede_cur.rowcount

        new_cur = conn.execute(
            "INSERT INTO extraction_runs (source_uri, content_hash, status) "
            "VALUES (?, ?, 'registered')",
            (body.source_uri, body.content_hash),
        )
        conn.commit()

        new_run_id: int = new_cur.lastrowid  # type: ignore[assignment]
        logger.info(
            "Source gate RE-EXTRACT: source_uri=%s old_run=%d new_run=%d "
            "superseded_assertions=%d",
            body.source_uri,
            prior_id,
            new_run_id,
            superseded_count,
        )
        response.status_code = status.HTTP_201_CREATED
        return ExtractionCheckResponse(
            action="re-extract",
            run_id=new_run_id,
            superseded_run_id=prior_id,
            superseded_assertion_count=superseded_count,
        )
    finally:
        conn.close()


@router.patch("/{run_id}", response_model=ExtractionRunItem)
def complete_extraction_run(
    run_id: int, body: ExtractionRunComplete
) -> ExtractionRunItem:
    """Mark an extraction run as completed or failed."""
    now = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with cortex_conn() as conn:
        existing = query(
            conn, f"SELECT {_COLS} FROM extraction_runs WHERE id = ?", (run_id,)
        )
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Extraction run not found: {run_id}",
            )

        conn.execute(
            "UPDATE extraction_runs SET status = ?, assertion_count = ?, completed_at = ? "
            "WHERE id = ?",
            (body.status, body.assertion_count, now, run_id),
        )
        conn.commit()
        rows = query(
            conn, f"SELECT {_COLS} FROM extraction_runs WHERE id = ?", (run_id,)
        )

    return ExtractionRunItem(**rows[0])


@router.get("", response_model=list[ExtractionRunItem])
def list_extraction_runs(
    source_uri: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=500),
) -> list[ExtractionRunItem]:
    """List extraction runs with optional source_uri and status filters."""
    clauses: list[str] = []
    params: list[str | int] = []

    if source_uri:
        clauses.append("source_uri = ?")
        params.append(source_uri)
    if status_filter:
        clauses.append("status = ?")
        params.append(status_filter)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {_COLS} FROM extraction_runs{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    return [ExtractionRunItem(**row) for row in rows]
