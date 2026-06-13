"""Handoff audit helpers — warn-only 2-B checks and verification snapshots."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .graph_utils import extract_entity_ids
from .handoff_paths import normalize_handoff_source_path, read_sandboxed_source_text


def _handoff_finding(kind: str, subject: str, detail: str) -> dict[str, Any]:
    from .dispatch_ops._detectors._shared import _finding

    return _finding(kind, subject, detail)


def check_handoff_prompt_in_source(
    *,
    handoff_prompt: str | None,
    source_path: str | None,
    files_root: Path,
) -> dict[str, Any] | None:
    """Warn-only consistency check (thread 1188, 2-B) — detached_string only."""
    if not handoff_prompt or not source_path:
        return None
    rel = normalize_handoff_source_path(source_path)
    text = read_sandboxed_source_text(files_root, source_path)
    if text is None:
        return _handoff_finding(
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
        return _handoff_finding(
            "handoff_prompt_source_mismatch",
            rel or str(source_path),
            (
                f"handoff_prompt is not a substring of its claimed source file "
                f"{rel!r} — the structured prompt may have drifted from, or "
                "been authored independently of, the authoritative .md file."
            ),
        )
    return None


def check_handoff_transcript_anchor(
    *,
    session_id: str,
    handoff_prompt: str | None,
    handoff_source_path: str | None = None,
) -> dict[str, Any] | None:
    """Warn when handoff_prompt omits the closing-session transcript anchor."""
    if not handoff_prompt or not handoff_prompt.strip():
        return None
    if handoff_source_path:
        norm = normalize_handoff_source_path(handoff_source_path) or ""
        if session_id in norm.replace("\\", "/"):
            return None
    entity_ref = f"transcript:{session_id}"
    file_ref = f"notes/system/transcripts/{session_id}"
    if entity_ref in handoff_prompt or file_ref in handoff_prompt:
        return None
    return _handoff_finding(
        "handoff_missing_transcript_anchor",
        entity_ref,
        (
            f"handoff_prompt omitted closing-session anchor ({entity_ref} or "
            f"{file_ref}.md). Next session may start without continuity."
        ),
    )


_DEFERRAL_CONTEXT_RE = re.compile(
    r"\b(?:deferred|planned|not\s+yet\s+created|future\s+work|to\s+be\s+created)\b",
    re.IGNORECASE,
)


def cited_entity_ids_in_prompt(
    handoff_prompt: str,
    *,
    session_id: str | None = None,
) -> set[str]:
    """Entity refs cited in the prompt, excluding the closing-session transcript."""
    ids = extract_entity_ids(handoff_prompt)
    if session_id:
        ids.discard(f"transcript:{session_id}")
    return ids


def is_deferred_entity_reference(handoff_prompt: str, entity_id: str) -> bool:
    """True when *entity_id* is cited as intentional future/planned work."""
    if entity_id not in handoff_prompt:
        return False
    for line in handoff_prompt.splitlines():
        if entity_id not in line:
            continue
        lowered = line.lower()
        if "deferred inventory" in lowered or _DEFERRAL_CONTEXT_RE.search(line):
            return True
    idx = handoff_prompt.find(entity_id)
    if idx < 0:
        return False
    window = handoff_prompt[max(0, idx - 80) : idx + len(entity_id) + 80]
    return bool(_DEFERRAL_CONTEXT_RE.search(window))


def _entity_phase_note(entity_type: str, attrs: dict[str, Any]) -> str:
    for key in ("phase", "contract", "plan_phase"):
        raw = attrs.get(key)
        if raw is not None and str(raw).strip():
            text = str(raw).strip()
            if key == "phase" and not text.endswith("-phase"):
                return f"{text}-phase"
            return text
    return entity_type


def _entity_state_line(
    entity_id: str,
    entity_type: str,
    workflow_state: str | None,
    attrs: dict[str, Any],
) -> str:
    state = workflow_state or "?"
    note = _entity_phase_note(entity_type, attrs)
    return f"{entity_id} state={state} (note: {note})"


def format_cited_entity_state_snapshot(
    handoff_prompt: str,
    *,
    session_id: str | None = None,
    conn: object | None = None,
) -> str | None:
    """Annotated workflow_state lines for cited entities (spec §4.3 / caveat C3)."""
    entity_ids = cited_entity_ids_in_prompt(handoff_prompt, session_id=session_id)
    if not entity_ids:
        return None

    lines: list[str] = []
    db_conn = conn
    owns_conn = db_conn is None
    if owns_conn:
        from .db import cortex_conn

        db_conn = cortex_conn()
    try:
        for entity_id in sorted(entity_ids):
            row = db_conn.execute(  # type: ignore[union-attr]
                "SELECT type, workflow_state, attributes FROM entities WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if row is None:
                lines.append(f"{entity_id} state=? (note: unresolved)")
                continue
            attrs: dict[str, Any] = {}
            if row["attributes"]:
                try:
                    attrs = json.loads(row["attributes"])
                except (json.JSONDecodeError, TypeError):
                    attrs = {}
            lines.append(
                _entity_state_line(
                    entity_id,
                    str(row["type"]),
                    row["workflow_state"],
                    attrs,
                )
            )
    finally:
        if owns_conn:
            db_conn.close()  # type: ignore[union-attr]

    return "; ".join(lines)
