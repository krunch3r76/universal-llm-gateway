"""Transcript-scoped handoff persistence helpers.

Mirrors ``session_journals.handoff_prompt`` onto ``transcript:{session_id}``
entity attributes so ``entity_get`` can retrieve it on explicit reference.
Boot omits handoffs — see ``decision:transcript-scoped-handoff-explicit-load``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from .db import json_encode

# Write-path tags stamped onto ``handoff_provenance.write_path`` so a reader
# can distinguish a lead-authored close from a post-close upsert.
WRITE_PATH_SESSION_CLOSE = "session_close"
WRITE_PATH_HANDOFF_UPSERT = "session_handoff_upsert"


def _normalize_source_path(source_path: str | None) -> str | None:
    """Strip a leading ``cortex:`` scheme + slashes from a cortex file path."""
    if not source_path:
        return None
    cleaned = source_path.strip()
    if cleaned.startswith("cortex:"):
        cleaned = cleaned[len("cortex:") :]
    return cleaned.lstrip("/") or None


def compute_source_file_sha256(
    files_root: Path,
    source_path: str | None,
) -> str | None:
    """Return ``sha256:<hex>`` of the cortex file at *source_path*.

    Returns None when no path is given, the path escapes ``files_root``, or
    the file is unreadable — provenance degrades gracefully rather than
    failing the close/upsert.
    """
    rel = _normalize_source_path(source_path)
    if rel is None:
        return None
    try:
        abs_path = (files_root / rel).resolve()
        abs_path.relative_to(files_root.resolve())
        text = abs_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def build_handoff_provenance(
    *,
    write_path: str,
    source_path: str | None,
    files_root: Path,
    written_at: str | None = None,
) -> dict[str, Any]:
    """Build the ``handoff_provenance`` block stamped on the transcript attribute.

    See agent-bus thread 1188 / decision:handoff-surface-consistency: the
    structured ``handoff_prompt`` string is authored independently of the
    lead ``.md`` file, so a reader needs the write path and (when present)
    the source file + its hash to tell a file-backed handoff from a
    detached or bled-through string.
    """
    written = written_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    rel = _normalize_source_path(source_path)
    return {
        "write_path": write_path,
        "written_at": written,
        "source_file": rel,
        "source_file_sha256": compute_source_file_sha256(files_root, rel),
    }


def _read_source_file_text(
    files_root: Path,
    source_path: str | None,
) -> str | None:
    """Return the sandboxed text of the cortex file at *source_path*, or None.

    Mirrors :func:`compute_source_file_sha256`'s path-sandbox + graceful
    degradation: None when no path is given, the path escapes ``files_root``,
    or the file is unreadable.
    """
    rel = _normalize_source_path(source_path)
    if rel is None:
        return None
    try:
        abs_path = (files_root / rel).resolve()
        abs_path.relative_to(files_root.resolve())
        return abs_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def check_handoff_prompt_in_source(
    *,
    handoff_prompt: str | None,
    source_path: str | None,
    files_root: Path,
) -> dict[str, Any] | None:
    """Warn-only consistency check for the close path (thread 1188, 2-B).

    Returns a ``_finding``-shaped dict when both *handoff_prompt* and
    *source_path* are supplied but the prompt text is not a substring of the
    named file (or the file is unreadable). Returns None otherwise — the
    check NEVER blocks the close (decision:handoff-surface-consistency).

    Only fires when both inputs are present: a detached-string handoff with
    no ``source_path`` is the surface-but-flag case handled by provenance
    (``source_file: null``), not a mismatch.
    """
    from .dispatch_ops._detectors._shared import _finding

    if not handoff_prompt or not source_path:
        return None
    rel = _normalize_source_path(source_path)
    text = _read_source_file_text(files_root, source_path)
    if text is None:
        return _finding(
            "handoff_prompt_source_mismatch",
            rel or str(source_path),
            (
                f"handoff_source_path {source_path!r} was supplied alongside a "
                "handoff_prompt, but the file is missing/unreadable or escapes "
                "the cortex files sandbox — the prompt could not be verified "
                "against its claimed source."
            ),
        )
    if handoff_prompt not in text:
        return _finding(
            "handoff_prompt_source_mismatch",
            rel or str(source_path),
            (
                f"handoff_prompt is not a substring of its claimed source file "
                f"{rel!r} — the structured prompt may have drifted from, or "
                "been authored independently of, the authoritative .md file."
            ),
        )
    return None


def merge_handoff_attribute(
    attributes: dict[str, Any],
    handoff_prompt: str | None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return *attributes* with ``handoff_prompt`` (+ provenance) set or removed."""
    merged = dict(attributes)
    if handoff_prompt:
        merged["handoff_prompt"] = handoff_prompt
        if provenance is not None:
            merged["handoff_provenance"] = provenance
    else:
        merged.pop("handoff_prompt", None)
        merged.pop("handoff_provenance", None)
    return merged


def mirror_handoff_to_transcript_entity(
    conn: object,
    session_id: str,
    handoff_prompt: str | None,
    provenance: dict[str, Any] | None = None,
) -> bool:
    """Upsert ``handoff_prompt`` (+ provenance) on the transcript entity.

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
