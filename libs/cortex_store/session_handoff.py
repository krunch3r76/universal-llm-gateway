"""Transcript-scoped handoff persistence helpers.

Mirrors ``session_journals.handoff_prompt`` onto ``transcript:{session_id}``
entity attributes so ``entity_get`` can retrieve it on explicit reference.
Boot omits handoffs — see ``decision:transcript-scoped-handoff-explicit-load``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from .db import json_encode


def merge_handoff_attribute(
    attributes: dict[str, Any],
    handoff_prompt: str | None,
) -> dict[str, Any]:
    """Return *attributes* with ``handoff_prompt`` set or removed."""
    merged = dict(attributes)
    if handoff_prompt:
        merged["handoff_prompt"] = handoff_prompt
    else:
        merged.pop("handoff_prompt", None)
    return merged


def mirror_handoff_to_transcript_entity(
    conn: object,
    session_id: str,
    handoff_prompt: str | None,
) -> bool:
    """Upsert ``handoff_prompt`` on the transcript entity when it exists.

    Returns True when the entity row was updated, False when absent (e.g.
    ``transcript_depth=none`` closes).
    """
    entity_id = f"transcript:{session_id}"
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT attributes FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        return False
    existing: dict[str, Any] = (
        json.loads(row["attributes"]) if row["attributes"] else {}
    )
    updated = merge_handoff_attribute(existing, handoff_prompt)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(  # type: ignore[union-attr]
        "UPDATE entities SET attributes = ?, updated_at = ? WHERE id = ?",
        (json_encode(updated), now, entity_id),
    )
    return True


def require_closed_journal_row(
    conn: object,
    session_id: str,
) -> dict[str, Any]:
    """Fetch the journal row for a closed session or raise 404."""
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT id, session_id, agent, handoff_prompt FROM session_journals "
        "WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No session journal for session_id {session_id!r}",
        )
    return dict(row)
