"""Session-close helper functions (transcript entities, edges, 422 envelope)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fastapi import HTTPException, status

from ..db import cortex_conn
from ..dispatch_ops._shared import (
    _AGENT_SLUG_EXAMPLES,
    _AGENT_SLUG_RE,
    _AGENT_SLUG_RE_SOURCE,
    _FILES_ROOT,
    _SESSION_ID_EXAMPLES,
    _SESSION_ID_RE,
    _SESSION_ID_RE_SOURCE,
)
from agent_seat.session_id import derive_session_id_from_timestamp

from ..session_close_validation import _emit_rejected, build_validation_error
from ..status_trait_read import entity_has_trait_columns
from ..status_trait_write import trait_insert_extras, transcript_birth_traits


def _parse_opened_at(transcript_id: str) -> str | None:
    """Derive ``opened_at`` ISO 8601 from transcript ID (ignores 3-hex suffix)."""
    match = re.search(
        r"(\d{4})-(\d{2})-(\d{2})-(\d{6})(?:-[0-9a-f]{3})?$",
        transcript_id,
    )
    if match:
        y, m, d, hhmmss = match.groups()
        return f"{y}-{m}-{d}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}Z"
    return None


def _stamp_transcript_timestamps(
    conn: object,
    transcript_id: str,
    *,
    opened_at: str | None = None,
    closed_at: str | None = None,
) -> None:
    """Merge ``opened_at`` / ``closed_at`` into the transcript entity's attributes."""
    if not opened_at and not closed_at:
        return
    entity_id = f"transcript:{transcript_id}"
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT attributes FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    existing: dict[str, object] = (
        json.loads(row["attributes"]) if row and row["attributes"] else {}
    )
    if opened_at:
        existing["opened_at"] = opened_at
    if closed_at:
        existing["closed_at"] = closed_at
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(  # type: ignore[union-attr]
        "UPDATE entities SET attributes = ?, updated_at = ? WHERE id = ?",
        (json_encode(existing), now, entity_id),
    )


def _derive_session_id(agent: str, timestamp: str) -> str:
    """Derive a session ID from agent + timestamp string.

    Accepts ISO 8601 (``2026-04-08T23:11:00Z``) or any string containing
    a ``YYYY-MM-DD`` date fragment.  Falls back to now if unparseable.
    Format: ``{agent}-YYYY-MM-DD-HHMMSS-{3hex}``.
    """
    return derive_session_id_from_timestamp(agent, timestamp)


def _ensure_transcript_entity(
    conn: object,
    transcript_id: str,
    agent: str,
    timestamp: str,
    source_uri: str | None = None,
) -> None:
    """INSERT OR IGNORE the transcript entity — idempotent.

    When *source_uri* is provided, also updates it on an existing entity
    (INSERT OR IGNORE won't overwrite an existing row).
    """
    entity_id = f"transcript:{transcript_id}"
    if source_uri is None:
        source_uri = f"files://notes/system/transcripts/{transcript_id}.md"
    traits = transcript_birth_traits()
    trait_cols, trait_vals = trait_insert_extras(conn, traits)  # type: ignore[arg-type]
    cols = ["id", "type", "name", "source_uri", "created_at", "updated_at"]
    vals: list[object] = [
        entity_id,
        "transcript",
        transcript_id,
        source_uri,
        timestamp,
        timestamp,
    ]
    if not entity_has_trait_columns(conn):  # type: ignore[arg-type]
        cols.insert(3, "status")
        vals.insert(3, traits.legacy_status)
    cols.extend(trait_cols)
    vals.extend(trait_vals)
    ph = ", ".join(["?"] * len(vals))
    conn.execute(  # type: ignore[union-attr]
        f"INSERT OR IGNORE INTO entities ({', '.join(cols)}) VALUES ({ph})",
        tuple(vals),
    )
    conn.execute(  # type: ignore[union-attr]
        "UPDATE entities SET source_uri = ?, updated_at = ? "
        "WHERE id = ? AND (source_uri IS NULL OR source_uri != ?)",
        (source_uri, timestamp, entity_id, source_uri),
    )


def _ensure_continues_edge(
    conn: object,
    session_id: str,
    prior_session_id: str,
    agent: str,
    timestamp: str,
) -> None:
    """Write a continues edge: transcript:{session_id} → transcript:{prior_session_id}.

    Checks for an existing active edge before inserting to stay idempotent.
    """
    from_node = f"transcript:{session_id}"
    to_node = f"transcript:{prior_session_id}"
    existing = conn.execute(  # type: ignore[union-attr]
        "SELECT 1 FROM session_edges "
        "WHERE from_node = ? AND to_node = ? AND edge_type = 'continues' "
        "AND valid_until IS NULL LIMIT 1",
        (from_node, to_node),
    ).fetchone()
    if existing:
        return
    conn.execute(  # type: ignore[union-attr]
        "INSERT INTO session_edges "
        "(session_id, agent, from_node, to_node, edge_type, strength, edge_source, created_at) "
        "VALUES (?, ?, ?, ?, 'continues', 0.9, 'server', ?)",
        (session_id, agent, from_node, to_node, timestamp),
    )


def _raise_422(
    *,
    reason: str,
    session_id: str,
    agent: str,
    detail: str,
    payload: dict | None = None,
) -> None:
    """Emit `mcp.session.close.rejected` and raise the matching 422 in one call.

    When ``payload`` is supplied it becomes the ``HTTPException.detail`` body
    so callers receive the full structured error (field/received/expected/
    examples/hint) per the cross-tool validation-error contract. The plain
    ``detail`` string is still used for the event payload (human-readable).
    """
    _emit_rejected(reason, session_id=session_id, agent=agent, detail=detail)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=payload if payload is not None else detail,
    )
