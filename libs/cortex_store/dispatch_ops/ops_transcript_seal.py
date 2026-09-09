"""Transcript seal dispatch op — succession harvest via session_close."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

from ..events_tape import (
    transcript_seal_refused_not_lane_window,
    transcript_sealed_by_succession,
)
from ..models import SessionCloseRequest
from ..routes.session_close import close_session
from ..session_close_successor_hop import conversation_uuid_from_jsonl_path
from ..transcript_assembly import TranscriptPathError, resolve_jsonl_path
from ..transcript_lane_touch import binding_for, dominant_lane, lane_touches
from ..transcript_session_id import derive_session_id_from_jsonl_start
from ..verbatim_succession import (
    divergence_index,
    load_sealed_payload_for_session,
    prefix_holds,
)
from .ops_transcript_discover import _resolve_explicit_uuids

logger = get_logger("cortex-api.dispatch_ops.transcript_seal")

_SUCCESSION_STUB = (
    "## Session Summary\n\n"
    "**Decisions:** (absent — succession harvest)\n"
    "**Open items:** (absent — succession harvest)\n"
)


def _agent_label_from_session_id(session_id: str) -> str:
    from agent_seat.session_id import agent_slug_from_session_id

    return agent_slug_from_session_id(session_id)


def _op_transcript_seal(
    thread: str | None = None,
    thread_id: str | None = None,
    jsonl_path: str | None = None,
    transcript_jsonl_path: str | None = None,
    session_id: str | None = None,
    binding: str | None = None,
    explicit_transcript_ids: list[str] | None = None,
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

    uuid = conversation_uuid_from_jsonl_path(resolved)
    explicit = _resolve_explicit_uuids(str(tid), explicit_transcript_ids)
    touches = lane_touches(resolved)
    computed_binding, computed_lane = binding_for(
        str(tid),
        touches,
        explicit_uuids=explicit,
        conversation_uuid=uuid,
    )
    if binding == "explicit_cp":
        effective_binding = "explicit_cp"
    else:
        effective_binding = computed_binding
    if effective_binding not in {"explicit_cp", "dominant_write"}:
        transcript_seal_refused_not_lane_window(
            thread_id=str(tid),
            conversation_uuid=uuid,
            binding=effective_binding,
        )
        return {
            "error": f"window does not bind to lane {tid}",
            "reason": "not_lane_window",
            "code": "transcript_seal.not_lane_window",
            "binding": effective_binding,
        }
    stamp_lane = computed_lane or dominant_lane(touches) or str(tid)

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
        lookup_journaled_by_conversation_uuid,
        lookup_sealed_journal,
    )

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
        from continuity_tape.extract_jsonl import extract_turns_from_jsonl
        from continuity_tape.render_md import render_verbatim_md

        try:
            envelope = extract_turns_from_jsonl(
                resolved, tools="marker", session_id=derived
            )
            new_verbatim_md, new_turns = render_verbatim_md(envelope, derived)
        except ValueError as exc:
            return {"error": str(exc), "reason": "jsonl_parse_error"}
        from ..dispatch_ops._shared import _FILES_ROOT

        sealed_payload = load_sealed_payload_for_session(
            derived, files_root=_FILES_ROOT
        )
        prior_turns = sealed_payload.sealed_turns if sealed_payload else 0
        if sealed_payload is not None:
            if sealed_payload.codec == "messages-v1":
                ok = prefix_holds(
                    "messages-v1",
                    sealed_payload.messages or [],
                    envelope.messages,
                )
            else:
                ok = prefix_holds(
                    "md-v1",
                    sealed_payload.verbatim_md,
                    new_verbatim_md,
                )
            if not ok:
                from ..events_tape import transcript_seal_verbatim_diverged

                first_idx = (
                    divergence_index(
                        "messages-v1",
                        sealed_payload.messages or [],
                        envelope.messages,
                    )
                    if sealed_payload.codec == "messages-v1"
                    else divergence_index(
                        "md-v1",
                        sealed_payload.verbatim_md,
                        new_verbatim_md,
                    )
                )
                transcript_seal_verbatim_diverged(
                    session_id=derived,
                    transcript_id=uuid,
                    mode="extend",
                    codec=sealed_payload.codec,
                    sealed_turns=prior_turns,
                    live_turns=new_turns,
                    first_divergent_index=first_idx,
                )
                return {
                    "reason": "already_closed",
                    "code": "transcript_seal.already_closed",
                    "session_id": derived,
                    "turn_count": prior_turns,
                    "live_turn_count": new_turns,
                    "divergence": "verbatim_diverged",
                }
        if new_turns <= prior_turns:
            return {
                "error": f"session {derived!r} already sealed",
                "reason": "already_closed",
                "code": "transcript_seal.already_closed",
                "session_id": derived,
                "turn_count": prior_turns,
            }
    now = datetime.now(tz=UTC).isoformat()
    entity_ids: list[str] = []
    if effective_binding in {"explicit_cp", "dominant_write"}:
        entity_ids.append(f"agent-bus:{tid}")
    body = SessionCloseRequest(
        session_id=derived,
        agent=agent,
        session_summary_md=_SUCCESSION_STUB,
        summary="Succession harvest seal for human continuity speech tape.",
        transcript_jsonl_path=jpath,
        transcript_depth="verbatim",
        entity_ids=entity_ids,
        closed_by="succession",
        assistant_label="Assistant",
        succession_seal_authority=True,
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
        dominant_lane=stamp_lane,
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
