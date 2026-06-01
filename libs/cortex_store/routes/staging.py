from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query, status
from universal_logging import get_logger

from ..db import cortex_conn, json_decode, json_encode, query
from ..models import (
    StagingApproval,
    StagingBatchCreate,
    StagingItem,
    StagingList,
)

if TYPE_CHECKING:
    import sqlite3

logger = get_logger("cortex-api.staging")
router = APIRouter(prefix="/staging", tags=["staging"])

_COLS = (
    "id, source_uri, proposal_type, proposal_action, target_id, "
    "proposal_json, chunk_id, status, resolved_to, reviewer, "
    "reviewed_at, created_at"
)

_ASSERTION_INSERT = (
    "INSERT INTO assertions ("
    "  entity_id, claim, confidence, evidence, evidence_uris,"
    "  chunk_id, derivation_type, reasoning_summary, observed_at,"
    "  valid_from, valid_until, validity_precision, confidence_score,"
    "  temporal_type, is_atomic, is_decontextualized"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _assertion_params(pj: dict, chunk_id: int | None) -> tuple:
    """Constructs a tuple of parameters for inserting into the assertions table.

    Args:
        pj: A dictionary containing proposal JSON data for an assertion.
        chunk_id: The ID of the chunk associated with the assertion, if any.

    Returns:
        A tuple of values ordered to match the `_ASSERTION_INSERT` SQL statement.
    """
    return (
        pj.get("entity_id"),
        pj.get("claim", ""),
        pj.get("confidence", "believed"),
        pj.get("evidence"),
        json_encode(pj.get("evidence_uris")),
        chunk_id,
        pj.get("derivation_type"),
        pj.get("reasoning_summary"),
        pj.get("observed_at"),
        pj.get("valid_from"),
        pj.get("valid_until"),
        pj.get("validity_precision"),
        pj.get("confidence_score"),
        pj.get("temporal_type"),
        pj.get("is_atomic", True),
        pj.get("is_decontextualized", True),
    )


def _decode_staging_row(row: dict) -> StagingItem:
    """Decodes a raw database row into a StagingItem model.

    Specifically, it decodes the 'proposal_json' field from a JSON string to a Python object.

    Args:
        row: A dictionary representing a row from the extraction_staging table.

    Returns:
        A StagingItem instance populated with the decoded data.
    """
    decoded = dict(row)
    decoded["proposal_json"] = json_decode(decoded.get("proposal_json"))
    return StagingItem(**decoded)


def _now() -> str:
    """Returns the current UTC datetime formatted as an ISO 8601 string.

    Returns:
        A string representing the current UTC time in 'YYYY-MM-DDTHH:MM:SSZ' format.
    """
    return datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("", response_model=StagingList)
def list_staging(
    status_filter: str | None = Query(None, alias="status"),
    source_uri: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> StagingList:
    """List staging proposals with optional status/source filters."""
    clauses: list[str] = []
    params: list[str | int] = []

    if status_filter:
        clauses.append("status = ?")
        params.append(status_filter)
    if source_uri:
        clauses.append("source_uri = ?")
        params.append(source_uri)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {_COLS} FROM extraction_staging{where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with cortex_conn() as conn:
        rows = query(conn, sql, tuple(params))

    return StagingList(items=[_decode_staging_row(row) for row in rows])


@router.get("/{staging_id}", response_model=StagingItem)
def get_staging(staging_id: int) -> StagingItem:
    """Get a single staging proposal."""
    with cortex_conn() as conn:
        rows = query(
            conn, f"SELECT {_COLS} FROM extraction_staging WHERE id = ?", (staging_id,)
        )

    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Not found: {staging_id}")
    return _decode_staging_row(rows[0])


@router.post("/batch", response_model=StagingList, status_code=status.HTTP_201_CREATED)
def create_staging_batch(body: StagingBatchCreate) -> StagingList:
    """Create multiple staging proposals in one request."""
    with cortex_conn() as conn:
        created_ids: list[int] = []
        for p in body.proposals:
            cur = conn.execute(
                "INSERT INTO extraction_staging "
                "(source_uri, proposal_type, proposal_action, target_id, "
                " proposal_json, chunk_id) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    p.source_uri,
                    p.proposal_type,
                    p.proposal_action,
                    p.target_id,
                    json_encode(p.proposal_json),
                    p.chunk_id,
                ),
            )
            if cur.lastrowid is None:
                logger.warning("No lastrowid returned after inserting staging item.")
                # Depending on desired behavior, could raise an error or skip
                # For now, we'll append -1 or handle it as an int
                created_ids.append(-1)  # Or raise an exception
            else:
                created_ids.append(cur.lastrowid)
        conn.commit()

        placeholders = ",".join("?" * len(created_ids))
        rows = query(
            conn,
            f"SELECT {_COLS} FROM extraction_staging WHERE id IN ({placeholders})",
            tuple(created_ids),
        )

    return StagingList(items=[_decode_staging_row(row) for row in rows])


def _fetch_pending(conn: sqlite3.Connection, staging_id: int) -> dict:
    """Fetches a staging proposal by ID and ensures its status is 'pending'.

    Args:
        conn: The SQLite database connection.
        staging_id: The ID of the staging proposal to fetch.

    Returns:
        A dictionary representing the pending staging proposal row.

    Raises:
        HTTPException: If the staging proposal is not found (404) or
                       if its status is not 'pending' (409 Conflict).
    """
    rows = query(
        conn, f"SELECT {_COLS} FROM extraction_staging WHERE id = ?", (staging_id,)
    )
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Not found: {staging_id}")
    if rows[0]["status"] != "pending":
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Already resolved: {rows[0]['status']}"
        )
    return rows[0]


@router.post("/{staging_id}/approve", response_model=StagingItem)
def approve_staging(
    staging_id: int, body: StagingApproval | None = None
) -> StagingItem:
    """Approve a staging proposal — writes to Cortex with full provenance."""
    now = _now()
    reviewer = body.reviewer if body else "human"

    with cortex_conn() as conn:
        proposal = _fetch_pending(conn, staging_id)
        resolved_to = _apply_proposal(conn, proposal)
        conn.execute(
            "UPDATE extraction_staging SET status = 'approved', "
            "resolved_to = ?, reviewer = ?, reviewed_at = ? WHERE id = ?",
            (resolved_to, reviewer, now, staging_id),
        )
        conn.commit()
        rows = query(
            conn, f"SELECT {_COLS} FROM extraction_staging WHERE id = ?", (staging_id,)
        )

    return _decode_staging_row(rows[0])


@router.post("/{staging_id}/reject", response_model=StagingItem)
def reject_staging(staging_id: int, body: StagingApproval | None = None) -> StagingItem:
    """Reject a staging proposal."""
    now = _now()
    reviewer = body.reviewer if body else "human"

    with cortex_conn() as conn:
        _fetch_pending(conn, staging_id)
        conn.execute(
            "UPDATE extraction_staging SET status = 'rejected', "
            "reviewer = ?, reviewed_at = ? WHERE id = ?",
            (reviewer, now, staging_id),
        )
        conn.commit()
        rows = query(
            conn, f"SELECT {_COLS} FROM extraction_staging WHERE id = ?", (staging_id,)
        )

    return _decode_staging_row(rows[0])


def _apply_proposal(conn: sqlite3.Connection, proposal: dict) -> str:
    """Apply an approved proposal to Cortex tables. Returns the resolved_to ID."""
    pj = json_decode(proposal["proposal_json"])
    if not pj:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty proposal_json")

    ptype, action = proposal["proposal_type"], proposal["proposal_action"]
    now = _now()
    chunk_id = proposal.get("chunk_id")

    if ptype == "entity" and action == "add":
        eid = pj.get("id")
        if not eid:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Entity 'id' is required for 'add' action"
            )
        # Fork D (G1, thread 1173): the confidence axis is derived from
        # assertions, not hand-set. A staged entity is born 'provisional'
        # (pending-review semantics, surfaced by the review-queue detector); a
        # hand-set confidence-axis value (e.g. 'confirmed') is ignored. Lifecycle
        # status is not applicable on the staging-add path.
        _proposed_status = pj.get("status")
        _staged_status = (
            _proposed_status
            if _proposed_status in ("merged", "deprecated", "reaped")
            else "provisional"
        )
        conn.execute(
            "INSERT INTO entities (id, type, name, description, status, "
            "aliases, attributes, notes, source_uri, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                eid,
                pj.get("type", ""),
                pj.get("name", ""),
                pj.get("description"),
                _staged_status,
                json_encode(pj.get("aliases")),
                json_encode(pj.get("attributes")),
                pj.get("notes"),
                pj.get("source_uri"),
                now,
                now,
            ),
        )
        return eid

    if ptype == "assertion" and action == "add":
        cur = conn.execute(_ASSERTION_INSERT, _assertion_params(pj, chunk_id))
        return str(cur.lastrowid)

    if ptype == "assertion" and action == "revise":
        target_id = proposal.get("target_id")
        if not target_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "revise needs target_id")
        conn.execute(
            "UPDATE assertions SET superseded_by = -1, superseded_at = ? WHERE id = ?",
            (now, target_id),
        )
        cur = conn.execute(_ASSERTION_INSERT, _assertion_params(pj, chunk_id))
        new_id = cur.lastrowid
        conn.execute(
            "UPDATE assertions SET superseded_by = ? WHERE id = ?",
            (new_id, target_id),
        )
        return str(new_id)

    if ptype == "assertion" and action == "remove":
        target_id = proposal.get("target_id")
        if not target_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "remove needs target_id")
        conn.execute(
            "UPDATE assertions SET superseded_by = -1, superseded_at = ? WHERE id = ?",
            (now, target_id),
        )
        return f"removed:{target_id}"

    raise HTTPException(
        status.HTTP_400_BAD_REQUEST, f"Unsupported: type={ptype}, action={action}"
    )
