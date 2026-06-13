"""Assemble the ``handoff_verification`` block from mechanical audit checks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .handoff_audit import (
    check_handoff_prompt_in_source,
    check_handoff_transcript_anchor,
    cited_entity_ids_in_prompt,
    format_cited_entity_state_snapshot,
    is_deferred_entity_reference,
)

_CHECK_PASS = "passed"
_CHECK_FAIL = "failed"


def _append_check(
    checks: list[dict[str, str]],
    *,
    name: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append(
        {
            "name": name,
            "status": _CHECK_PASS if passed else _CHECK_FAIL,
            "detail": detail,
        }
    )


def build_handoff_verification(
    *,
    session_id: str,
    handoff_prompt: str | None,
    handoff_source_path: str | None,
    files_root: Path,
    conn: object | None = None,
) -> dict[str, Any] | None:
    """Return ``{checks, passed, total}`` for a resolved handoff prompt."""
    if not handoff_prompt or not handoff_prompt.strip():
        return None

    checks: list[dict[str, str]] = []

    anchor_gap = check_handoff_transcript_anchor(
        session_id=session_id,
        handoff_prompt=handoff_prompt,
        handoff_source_path=handoff_source_path,
    )
    _append_check(
        checks,
        name="transcript_anchor_present",
        passed=anchor_gap is None,
        detail=(
            anchor_gap["detail"]
            if anchor_gap is not None
            else "closing-session anchor present"
        ),
    )

    entity_ids = cited_entity_ids_in_prompt(handoff_prompt, session_id=session_id)
    unresolved: list[str] = []
    deferred: list[str] = []
    if entity_ids:
        db_conn = conn
        owns_conn = db_conn is None
        if owns_conn:
            from .db import cortex_conn

            db_conn = cortex_conn()
        try:
            for entity_id in sorted(entity_ids):
                row = db_conn.execute(  # type: ignore[union-attr]
                    "SELECT id FROM entities WHERE id = ?", (entity_id,)
                ).fetchone()
                if row is None:
                    if is_deferred_entity_reference(handoff_prompt, entity_id):
                        deferred.append(entity_id)
                    else:
                        unresolved.append(entity_id)
        finally:
            if owns_conn:
                db_conn.close()  # type: ignore[union-attr]

    if unresolved:
        resolvability_detail = f"unresolved: {', '.join(unresolved)}"
    elif deferred:
        resolvability_detail = (
            f"all cited entities resolve; deferred (planned): {', '.join(deferred)}"
        )
    else:
        resolvability_detail = "all cited entities resolve"

    _append_check(
        checks,
        name="cited_entities_resolvable",
        passed=not unresolved,
        detail=resolvability_detail,
    )

    snapshot = format_cited_entity_state_snapshot(
        handoff_prompt,
        session_id=session_id,
        conn=conn,
    )
    _append_check(
        checks,
        name="cited_entity_state_snapshot",
        passed=True,
        detail=snapshot or "no cited entities",
    )

    if handoff_source_path:
        mismatch = check_handoff_prompt_in_source(
            handoff_prompt=handoff_prompt,
            source_path=handoff_source_path,
            files_root=files_root,
        )
        _append_check(
            checks,
            name="prompt_in_source",
            passed=mismatch is None,
            detail=(
                mismatch["detail"]
                if mismatch is not None
                else "handoff_prompt is a substring of source file"
            ),
        )

    passed = sum(1 for c in checks if c["status"] == _CHECK_PASS)
    return {"checks": checks, "passed": passed, "total": len(checks)}
