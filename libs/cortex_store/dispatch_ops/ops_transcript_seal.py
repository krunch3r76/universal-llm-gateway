"""Transcript seal dispatch op — succession harvest via session_close."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from ..events_tape import transcript_sealed_by_succession
from ..models import SessionCloseRequest
from ..routes.session_close import close_session
from ..transcript_assembly import TranscriptPathError, resolve_jsonl_path
from ..transcript_session_id import derive_session_id_from_jsonl_start
from ..verbatim_succession import (
    journal_verbatim_bytes,
    split_verbatim_layer,
)

logger = get_logger("cortex-api.dispatch_ops.transcript_seal")

_SUCCESSION_STUB = (
    "## Session Summary\n\n"
    "**Decisions:** (absent — succession harvest)\n"
    "**Open items:** (absent — succession harvest)\n"
)


def _agent_label_from_session_id(session_id: str) -> str:
    prefix = session_id.split("-", 1)[0]
    return prefix or "cursor"


def _op_transcript_seal(
    thread: str | None = None,
    thread_id: str | None = None,
    jsonl_path: str | None = None,
    transcript_jsonl_path: str | None = None,
    session_id: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Seal an idle window via session_close with ``closed_by=succession``."""
    tid = thread or thread_id
    jpath = jsonl_path or transcript_jsonl_path
    if not tid:
        return {"error": "thread is required", "reason": "missing_arg", "code": "transcript_seal.missing_thread"}
    if not jpath:
        return {"error": "jsonl_path is required", "reason": "missing_arg", "code": "transcript_seal.missing_jsonl"}

    try:
        resolved = resolve_jsonl_path(jpath)
    except TranscriptPathError as exc:
        return {"error": str(exc), "reason": "jsonl_invalid", "code": "transcript_seal.live"}

    derived = session_id or derive_session_id_from_jsonl_start(
        jsonl_path=resolved, agent="cursor"
    )
    if not derived:
        return {
            "error": "could not derive session_id from JSONL",
            "reason": "hollow",
            "code": "transcript_seal.hollow",
        }

    from ..session_close_successor_hop import (
        conversation_uuid_from_jsonl_path,
        lookup_journaled_by_conversation_uuid,
        lookup_sealed_journal,
    )

    uuid = conversation_uuid_from_jsonl_path(resolved)
    human_closed = lookup_journaled_by_conversation_uuid(uuid)
    if human_closed is not None:
        return {
            "error": f"session {human_closed.session_id!r} already sealed",
            "reason": "already_closed",
            "code": "transcript_seal.already_closed",
            "session_id": human_closed.session_id,
        }

    sealed = lookup_sealed_journal(derived)
    if sealed is not None and sealed.closed_by != "succession":
        return {
            "error": f"session {derived!r} already sealed",
            "reason": "already_closed",
            "code": "transcript_seal.already_closed",
            "session_id": derived,
        }

    agent = _agent_label_from_session_id(derived)
    if sealed is not None and sealed.closed_by == "succession":
        from ..transcript_assembly import assemble_verbatim_md

        try:
            new_verbatim, new_turns = assemble_verbatim_md(
                jsonl_path=resolved,
                session_id=derived,
            )
        except ValueError as exc:
            return {"error": str(exc), "reason": "jsonl_parse_error"}
        from ..dispatch_ops._shared import _FILES_ROOT

        prior_turns = 0
        from ..db import cortex_conn

        with cortex_conn() as conn:
            row = conn.execute(
                "SELECT file_path, verbatim_bytes FROM session_journals WHERE session_id = ?",
                (derived,),
            ).fetchone()
        if row and row["file_path"]:
            prior_path = _FILES_ROOT / row["file_path"]
            if prior_path.is_file():
                prior_text = prior_path.read_text(encoding="utf-8")
                prior_verbatim = split_verbatim_layer(
                    prior_text,
                    verbatim_bytes=journal_verbatim_bytes(row),
                )
                prior_turns = sum(
                    1
                    for line in prior_verbatim.splitlines()
                    if line.startswith("## Turn")
                )
        if new_turns <= prior_turns:
            return {
                "error": f"session {derived!r} already sealed",
                "reason": "already_closed",
                "code": "transcript_seal.already_closed",
                "session_id": derived,
                "turn_count": prior_turns,
            }
    now = datetime.now(tz=UTC).isoformat()
    body = SessionCloseRequest(
        session_id=derived,
        agent=agent,
        session_summary_md=_SUCCESSION_STUB,
        summary="Succession harvest seal for human continuity speech tape.",
        transcript_jsonl_path=jpath,
        transcript_depth="verbatim",
        entity_ids=[str(tid), f"agent-bus:{tid}"],
        closed_by="succession",
        assistant_label="Assistant",
    )
    try:
        response = close_session(body)
    except Exception as exc:
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, dict):
                code = detail.get("reason") or detail.get("code") or "transcript_seal.refused"
                return {"error": detail.get("error") or str(detail), "reason": code, "code": code}
            return {"error": str(detail), "reason": "refused", "code": "transcript_seal.refused"}
        raise

    _stamp_succession_fields(
        session_id=derived,
        sealed_by=agent,
        sealed_on=now,
        conversation_uuid=uuid,
        dominant_lane=str(tid),
    )
    transcript_sealed_by_succession(
        session_id=derived,
        thread_id=str(tid),
        conversation_uuid=uuid,
        turn_count=response.turn_count,
    )
    return {
        "session_id": derived,
        "journal_row_id": response.journal_row_id,
        "transcript_entity_id": response.transcript_entity_id,
        "content_hash": response.content_hash,
        "turn_count": response.turn_count,
        "conversation_uuid": uuid,
        "closed_by": "succession",
        "sealed_by": agent,
        "sealed_on": now,
    }


def _stamp_succession_fields(
    *,
    session_id: str,
    sealed_by: str,
    sealed_on: str,
    conversation_uuid: str,
    dominant_lane: str | None = None,
) -> None:
    from ..db import cortex_conn

    conn = cortex_conn()
    try:
        conn.execute(
            "UPDATE session_journals SET closed_by = ?, sealed_by = ?, "
            "sealed_on = ?, conversation_uuid = ?, "
            "dominant_lane = COALESCE(?, dominant_lane) WHERE session_id = ?",
            (
                "succession",
                sealed_by,
                sealed_on,
                conversation_uuid,
                dominant_lane,
                session_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


__all__ = ["_op_transcript_seal", "_stamp_succession_fields"]
