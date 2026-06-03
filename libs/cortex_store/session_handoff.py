"""Transcript-scoped handoff persistence helpers.

Mirrors ``session_journals.handoff_prompt`` onto ``transcript:{session_id}``
entity attributes. Thread 1188 (2-A v2): marker extraction lives in
``handoff_marker``; resolution in ``handoff_resolution``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from .db import json_encode
from .handoff_audit import check_handoff_prompt_in_source
from .handoff_derivation import WRITE_PATH_SESSION_CLOSE
from .handoff_marker import ExtractResult, extract_handoff_marker_region
from .handoff_provenance import build_handoff_provenance, compute_source_file_sha256
from .handoff_resolution import (
    DERIVATION_DETACHED_STRING,
    DERIVATION_SECTION,
    DERIVATION_SECTION_AMBIGUOUS,
    DERIVATION_SECTION_UNRESOLVED,
    HANDOFF_PROMPT_MAX_CHARS,
    HANDOFF_PROVENANCE_JSON_MAX_BYTES,
    HandoffResolution,
    handoff_dry_run_preview,
    handoff_post_close_findings,
    read_handoff_source_file,
    resolve_handoff_for_write,
)

WRITE_PATH_HANDOFF_UPSERT = "session_handoff_upsert"

__all__ = [
    "DERIVATION_DETACHED_STRING",
    "DERIVATION_SECTION",
    "DERIVATION_SECTION_AMBIGUOUS",
    "DERIVATION_SECTION_UNRESOLVED",
    "ExtractResult",
    "HANDOFF_PROMPT_MAX_CHARS",
    "HANDOFF_PROVENANCE_JSON_MAX_BYTES",
    "HandoffResolution",
    "WRITE_PATH_HANDOFF_UPSERT",
    "WRITE_PATH_SESSION_CLOSE",
    "build_handoff_provenance",
    "check_handoff_prompt_in_source",
    "compute_source_file_sha256",
    "extract_handoff_marker_region",
    "handoff_dry_run_preview",
    "handoff_post_close_findings",
    "merge_handoff_attribute",
    "mirror_handoff_to_transcript_entity",
    "read_handoff_source_file",
    "require_closed_journal_row",
    "resolve_handoff_for_write",
]


def merge_handoff_attribute(
    attributes: dict[str, Any],
    handoff_prompt: str | None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return *attributes* with handoff fields set or removed."""
    merged = dict(attributes)
    if handoff_prompt:
        merged["handoff_prompt"] = handoff_prompt
    else:
        merged.pop("handoff_prompt", None)
    if provenance is not None:
        merged["handoff_provenance"] = provenance
    elif not handoff_prompt:
        merged.pop("handoff_provenance", None)
    return merged


def mirror_handoff_to_transcript_entity(
    conn: object,
    session_id: str,
    handoff_prompt: str | None,
    provenance: dict[str, Any] | None = None,
) -> bool:
    """Upsert ``handoff_prompt`` (+ provenance) on the transcript entity."""
    entity_id = f"transcript:{session_id}"
    row = conn.execute(  # type: ignore[union-attr]
        "SELECT attributes FROM entities WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        return False
    existing: dict[str, Any] = (
        json.loads(row["attributes"]) if row["attributes"] else {}
    )
    updated = merge_handoff_attribute(existing, handoff_prompt, provenance)
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
