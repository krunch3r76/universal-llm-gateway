"""Transcript seal dispatch op — succession harvest via session_close."""

from __future__ import annotations

import hashlib
import json
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
    build_seal_envelope_meta,
    divergence_index,
    load_sealed_payload_for_session,
    prefix_holds,
    stamp_verbatim_fields,
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


def _messages_sha_on_disk(session_id: str) -> str | None:
    """Bare messages sha from the seal file. A prefixed content hash is absent."""
    from continuity_tape.handoff import bare_messages_sha

    from ._shared import _FILES_ROOT

    path = _FILES_ROOT / "notes" / "system" / "seals" / f"{session_id}.messages.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        return None
    return bare_messages_sha(meta.get("messages_sha256"))


def _letter_fields(response: Any, *, supplied: bool) -> dict[str, Any]:
    if getattr(response, "already_closed", False):
        status = "dropped_already_closed" if supplied else "none"
        return {"letter_status": status}
    status = getattr(response, "letter_status", None) or (
        "none" if not supplied else None
    )
    return {
        "letter_status": status or "none",
        "handoff_sha12": getattr(response, "handoff_sha12", None),
        "handoff_mission": getattr(response, "handoff_mission", None),
        "handoff_next_act": getattr(response, "handoff_next_act", None),
        "handoff_lag_turns": getattr(response, "handoff_lag_turns", None),
    }


def _non_cursor_seal(*, surface: str | None, messages: object | None) -> bool:
    if messages is not None:
        return True
    surf = (surface or "").strip().lower()
    return bool(surf and surf != "cursor")


def _coerce_messages_list(
    messages: list[dict[str, Any]] | dict[str, Any] | None,
) -> list[dict[str, Any]] | None:
    if messages is None:
        return None
    if isinstance(messages, dict):
        raw = messages.get("messages")
        if isinstance(raw, list):
            return [m for m in raw if isinstance(m, dict)]
        return None
    if isinstance(messages, list):
        return [m for m in messages if isinstance(m, dict)]
    return None


def _turn_count_from_messages(messages: list[dict[str, Any]]) -> int:
    indices = [
        int(m.get("turn_index") or 0)
        for m in messages
        if isinstance(m, dict) and m.get("turn_index") is not None
    ]
    if indices:
        return max(indices)
    return sum(1 for m in messages if m.get("role") == "user")


def _source_sha256_for_ref(raw_export_ref: str | None) -> str | None:
    if not raw_export_ref or not str(raw_export_ref).strip():
        return None
    from ._shared import _FILES_ROOT

    path = _FILES_ROOT / str(raw_export_ref).lstrip("/")
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seal_rel_path(session_id: str) -> str:
    return f"notes/system/seals/{session_id}.messages.json"


def _envelope_meta_surface(surface: str) -> str:
    """Map caller ``surface`` (e.g. ``grok-bot``) to ``EnvelopeMeta`` literals."""
    normalized = surface.strip().lower()
    if normalized in {"grok-bot", "grok_bot"}:
        return "grok"
    if normalized in {"claude.ai", "claude-ai", "claude_ai"}:
        return "claude_ai"
    if normalized in {"cursor", "claude_ai", "grok", "mixed"}:
        return normalized
    return "grok"


def _write_messages_seal_verified(
    *,
    session_id: str,
    messages: list[dict[str, Any]],
    agent: str,
    surface: str,
    transcript_id: str,
    source_sha256: str | None,
    sealed_at: str,
) -> dict[str, Any] | None:
    """Write ``messages-v1`` seal JSON; return error dict or ``None`` on success."""
    from continuity_tape.messages import (
        ContinuityMessagesEnvelope,
        EnvelopeMeta,
        seal_messages_sha256,
    )
    from continuity_tape.seal_reader import load_sealed_envelope_from_path
    from durable_io.atomic import durable_write_text

    from ._shared import _FILES_ROOT

    seal_rel = _seal_rel_path(session_id)
    seal_abs = _FILES_ROOT / seal_rel
    meta_surface = _envelope_meta_surface(surface)
    turn_count = _turn_count_from_messages(messages)
    envelope = ContinuityMessagesEnvelope(
        messages=messages,
        meta=EnvelopeMeta(
            surface=meta_surface,
            transcript_id=transcript_id,
            turn_count=turn_count,
            message_count=len(messages),
            source_sha256=source_sha256,
            messages_sha256=seal_messages_sha256(messages),
        ),
    )
    sealed_envelope = build_seal_envelope_meta(
        envelope,
        session_id=session_id,
        tools="openai",
        sealed_at=sealed_at,
        source_sha256=source_sha256,
        surface=surface,
    )
    payload = (
        json.dumps(
            sealed_envelope.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    try:
        durable_write_text(seal_abs, payload, retain_store_root=_FILES_ROOT)
    except OSError as exc:
        return {
            "error": f"seal write failed: {exc}",
            "reason": "refused",
            "code": "transcript_seal.refused",
        }
    readback = load_sealed_envelope_from_path(seal_abs)
    expected_sha = seal_messages_sha256(messages)
    if (
        readback is None
        or readback.meta.message_count != len(messages)
        or (messages and readback.meta.message_count == 0)
        or readback.meta.messages_sha256 != expected_sha
    ):
        seal_abs.unlink(missing_ok=True)
        return {
            "error": "seal readback failed or message_count mismatch",
            "reason": "empty_readback",
            "code": "transcript_seal.empty_readback",
            "session_id": session_id,
        }
    return None


def _commit_pretranscribed_journal(
    *,
    session_id: str,
    agent: str,
    summary: str,
    transcript_id: str,
    thread_id: str,
    stamp_lane: str,
    messages: list[dict[str, Any]],
    sealed_at: str,
    extend: bool,
) -> int:
    from ..db import cortex_conn, json_encode
    from ..routes.session_close_helpers import _ensure_transcript_entity

    from ._shared import _FILES_ROOT

    seal_rel = _seal_rel_path(session_id)
    entity_ids = [f"agent-bus:{thread_id}"]
    conn = cortex_conn()
    try:
        if extend:
            stamp_verbatim_fields(
                conn,
                session_id=session_id,
                codec="messages-v1",
                payload=messages,
            )
            conn.execute(
                "UPDATE session_journals SET summary = ?, entity_ids = ?, "
                "conversation_uuid = ?, dominant_lane = COALESCE(?, dominant_lane) "
                "WHERE session_id = ?",
                (
                    summary,
                    json_encode(entity_ids),
                    transcript_id,
                    stamp_lane,
                    session_id,
                ),
            )
            row = conn.execute(
                "SELECT id FROM session_journals WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            journal_row_id = int(row["id"]) if row else 0
        else:
            cur = conn.execute(
                "INSERT INTO session_journals "
                "(timestamp, agent, summary, entity_ids, file_path, session_id, "
                "closed_by, sealed_by, sealed_on, conversation_uuid, dominant_lane) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sealed_at,
                    agent,
                    summary,
                    json_encode(entity_ids),
                    seal_rel,
                    session_id,
                    "succession",
                    agent,
                    sealed_at,
                    transcript_id,
                    stamp_lane,
                ),
            )
            journal_row_id = int(cur.lastrowid or 0)
            stamp_verbatim_fields(
                conn,
                session_id=session_id,
                codec="messages-v1",
                payload=messages,
            )
        _ensure_transcript_entity(
            conn,
            session_id,
            agent,
            sealed_at,
            source_uri=f"files://{seal_rel}",
        )
        conn.commit()
        return journal_row_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _op_transcript_seal_pretranscribed(
    *,
    tid: str,
    session_id: str | None,
    agent: str | None,
    surface: str | None,
    transcript_id: str | None,
    messages: list[dict[str, Any]] | dict[str, Any] | None,
    raw_export_ref: str | None,
    handoff: dict[str, Any] | None,
    binding: str | None,
) -> dict[str, Any]:
    from continuity_tape.handoff import bare_messages_sha
    from continuity_tape.messages import seal_messages_sha256

    from ..dispatch_ops._shared import _FILES_ROOT
    from ..session_close_successor_hop import (
        lookup_journaled_by_conversation_uuid,
        lookup_sealed_journal,
    )

    if not session_id:
        return {
            "error": "session_id is required for non-Cursor seal",
            "reason": "missing_session_id",
            "code": "transcript_seal.missing_session_id",
        }
    if not agent:
        return {
            "error": "agent is required for non-Cursor seal",
            "reason": "missing_arg",
            "code": "transcript_seal.missing_agent",
        }
    if not surface:
        return {
            "error": "surface is required for non-Cursor seal",
            "reason": "missing_arg",
            "code": "transcript_seal.missing_surface",
        }
    msg_list = _coerce_messages_list(messages)
    if msg_list is None:
        return {
            "error": "messages must be a list or envelope dict",
            "reason": "missing_arg",
            "code": "transcript_seal.missing_messages",
        }
    if not msg_list:
        return {
            "error": "messages must be non-empty",
            "reason": "hollow",
            "code": "transcript_seal.hollow",
        }

    conv_id = (transcript_id or session_id).strip()
    if binding not in {None, "explicit_cp", "dominant_write"}:
        return {
            "error": f"window does not bind to lane {tid}",
            "reason": "not_lane_window",
            "code": "transcript_seal.not_lane_window",
            "binding": binding,
        }

    human_closed = lookup_journaled_by_conversation_uuid(conv_id)
    if human_closed is not None:
        return {
            "error": f"session {human_closed.session_id!r} already sealed",
            "reason": "already_closed",
            "code": "transcript_seal.already_closed",
            "session_id": human_closed.session_id,
        }

    derived = session_id
    sealed = lookup_sealed_journal(derived)
    if sealed is not None and sealed.closed_by != "succession":
        return {
            "error": f"session {derived!r} already sealed",
            "reason": "already_closed",
            "code": "transcript_seal.already_closed",
            "session_id": derived,
        }

    new_turns = _turn_count_from_messages(msg_list)
    extend = False
    if sealed is not None and sealed.closed_by == "succession":
        sealed_payload = load_sealed_payload_for_session(
            derived, files_root=_FILES_ROOT
        )
        prior_turns = sealed_payload.sealed_turns if sealed_payload else 0
        if sealed_payload is not None:
            ok = prefix_holds(
                "messages-v1",
                sealed_payload.messages or [],
                msg_list,
            )
            if not ok:
                from ..events_tape import transcript_seal_verbatim_diverged

                first_idx = divergence_index(
                    "messages-v1",
                    sealed_payload.messages or [],
                    msg_list,
                )
                transcript_seal_verbatim_diverged(
                    session_id=derived,
                    transcript_id=conv_id,
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
                    "messages_sha256": _messages_sha_on_disk(derived),
                    "letter_status": (
                        "dropped_already_closed"
                        if isinstance(handoff, dict)
                        else "none"
                    ),
                }
        if new_turns <= prior_turns:
            return {
                "error": f"session {derived!r} already sealed",
                "reason": "already_closed",
                "code": "transcript_seal.already_closed",
                "session_id": derived,
                "turn_count": prior_turns,
                "messages_sha256": _messages_sha_on_disk(derived),
                "letter_status": (
                    "dropped_already_closed" if isinstance(handoff, dict) else "none"
                ),
            }
        extend = True

    now = datetime.now(tz=UTC).isoformat()
    source_sha = _source_sha256_for_ref(raw_export_ref)
    write_err = _write_messages_seal_verified(
        session_id=derived,
        messages=msg_list,
        agent=agent,
        surface=surface,
        transcript_id=conv_id,
        source_sha256=source_sha,
        sealed_at=now,
    )
    if write_err is not None:
        return write_err

    summary = "Succession harvest seal for pre-transcribed continuity tape."
    journal_row_id = _commit_pretranscribed_journal(
        session_id=derived,
        agent=agent,
        summary=summary,
        transcript_id=conv_id,
        thread_id=tid,
        stamp_lane=tid,
        messages=msg_list,
        sealed_at=now,
        extend=extend,
    )
    _stamp_succession_fields(
        session_id=derived,
        sealed_by=agent,
        sealed_on=now,
        conversation_uuid=conv_id,
        dominant_lane=tid,
    )
    transcript_sealed_by_succession(
        session_id=derived,
        thread_id=tid,
        conversation_uuid=conv_id,
        turn_count=new_turns,
    )
    msg_sha = seal_messages_sha256(msg_list)
    return {
        "session_id": derived,
        "journal_row_id": journal_row_id,
        "transcript_entity_id": f"transcript:{derived}",
        "content_hash": None,
        "composed_sha256": None,
        "messages_sha256": bare_messages_sha(msg_sha),
        "turn_count": new_turns,
        "conversation_uuid": conv_id,
        "transcript_id": conv_id,
        "surface": surface,
        "message_count": len(msg_list),
        "source_sha256": source_sha,
        "raw_export_ref": raw_export_ref,
        "closed_by": "succession",
        "sealed_by": agent,
        "sealed_on": now,
        "letter_status": "none",
    }


def _op_transcript_seal(
    thread: str | None = None,
    thread_id: str | None = None,
    jsonl_path: str | None = None,
    transcript_jsonl_path: str | None = None,
    session_id: str | None = None,
    binding: str | None = None,
    explicit_transcript_ids: list[str] | None = None,
    handoff: dict[str, Any] | None = None,
    transcript_id: str | None = None,
    surface: str | None = None,
    messages: list[dict[str, Any]] | dict[str, Any] | None = None,
    raw_export_ref: str | None = None,
    agent: str | None = None,
    **_: object,
) -> dict[str, Any]:
    """Seal an idle window via session_close with ``closed_by=succession``."""
    tid = thread or thread_id
    if not tid:
        return {
            "error": "thread is required",
            "reason": "missing_arg",
            "code": "transcript_seal.missing_thread",
        }

    if _non_cursor_seal(surface=surface, messages=messages):
        return _op_transcript_seal_pretranscribed(
            tid=str(tid),
            session_id=session_id,
            agent=agent,
            surface=surface,
            transcript_id=transcript_id,
            messages=messages,
            raw_export_ref=raw_export_ref,
            handoff=handoff,
            binding=binding,
        )

    jpath = jsonl_path or transcript_jsonl_path
    if not jpath:
        return {
            "error": "jsonl_path is required",
            "reason": "missing_arg",
            "code": "transcript_seal.missing_jsonl",
        }

    try:
        resolved = resolve_jsonl_path(jpath)
    except TranscriptPathError as exc:
        return {
            "error": str(exc),
            "reason": "jsonl_invalid",
            "code": "transcript_seal.live",
        }

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

    cursor_agent = _agent_label_from_session_id(derived)
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
                    "messages_sha256": _messages_sha_on_disk(derived),
                    "letter_status": (
                        "dropped_already_closed"
                        if isinstance(handoff, dict)
                        else "none"
                    ),
                }
        if new_turns <= prior_turns:
            return {
                "error": f"session {derived!r} already sealed",
                "reason": "already_closed",
                "code": "transcript_seal.already_closed",
                "session_id": derived,
                "turn_count": prior_turns,
                "messages_sha256": _messages_sha_on_disk(derived),
                "letter_status": (
                    "dropped_already_closed" if isinstance(handoff, dict) else "none"
                ),
            }
    now = datetime.now(tz=UTC).isoformat()
    entity_ids: list[str] = []
    if effective_binding in {"explicit_cp", "dominant_write"}:
        entity_ids.append(f"agent-bus:{tid}")
    body = SessionCloseRequest(
        session_id=derived,
        agent=cursor_agent,
        session_summary_md=_SUCCESSION_STUB,
        summary="Succession harvest seal for human continuity speech tape.",
        transcript_jsonl_path=jpath,
        transcript_depth="verbatim",
        entity_ids=entity_ids,
        closed_by="succession",
        assistant_label="Assistant",
        succession_seal_authority=True,
        handoff=handoff if isinstance(handoff, dict) else None,
        stamp_thread=str(tid),
        stamp_surface=surface or "cursor",
        stamp_transcript_id=transcript_id or uuid,
    )
    try:
        response = close_session(body)
    except Exception as exc:
        from fastapi import HTTPException

        if isinstance(exc, HTTPException):
            detail = exc.detail
            if isinstance(detail, dict):
                code = (
                    detail.get("reason")
                    or detail.get("code")
                    or "transcript_seal.refused"
                )
                return {
                    "error": detail.get("error") or str(detail),
                    "reason": code,
                    "code": code,
                }
            return {
                "error": str(detail),
                "reason": "refused",
                "code": "transcript_seal.refused",
            }
        raise

    _stamp_succession_fields(
        session_id=derived,
        sealed_by=cursor_agent,
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
    from continuity_tape.handoff import bare_messages_sha

    return {
        "session_id": derived,
        "journal_row_id": response.journal_row_id,
        "transcript_entity_id": response.transcript_entity_id,
        "content_hash": response.content_hash,
        "composed_sha256": response.content_hash,
        "messages_sha256": bare_messages_sha(response.messages_sha256),
        "turn_count": response.turn_count,
        "conversation_uuid": uuid,
        "closed_by": "succession",
        "sealed_by": cursor_agent,
        "sealed_on": now,
        **_letter_fields(response, supplied=isinstance(handoff, dict)),
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
