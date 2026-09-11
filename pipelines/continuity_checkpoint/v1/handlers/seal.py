"""Seal transcript window before CHECKPOINT post (succession harvest)."""

from __future__ import annotations

import json
import logging
from typing import Any, override

from continuity_tape.events import stargate_continuity_checkpoint_sealed
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import cdp_ask_harvest, cortex_dispatch, step_output_json
from .seal_claude_ai import seal_claude_ai

logger = logging.getLogger(__name__)


def _count_turns_from_journal(session_id: str) -> int | None:
    from cortex_store.db import cortex_conn
    from cortex_store.dispatch_ops._shared import _FILES_ROOT
    from cortex_store.transcript_assembly import count_canonical_turn_headings
    from cortex_store.verbatim_succession import (
        journal_verbatim_bytes,
        split_verbatim_layer,
    )

    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT file_path, verbatim_bytes, verbatim_codec FROM session_journals "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not row or not row["file_path"]:
        return None
    path = _FILES_ROOT / row["file_path"]
    if not path.is_file():
        return None
    prior_text = path.read_text(encoding="utf-8")
    codec = row["verbatim_codec"] or "md-v1"
    if codec == "messages-v1":
        prior_verbatim = split_verbatim_layer(prior_text)
    else:
        prior_verbatim = split_verbatim_layer(
            prior_text,
            verbatim_bytes=journal_verbatim_bytes(row),
        )
    return count_canonical_turn_headings(prior_verbatim)


class ContinuityCheckpointSealHandler(BaseHandler):
    """Invoke cortex transcript_seal and map success/refusal shapes."""

    step_type = "continuity_checkpoint_seal_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        thread = str(options.get("thread") or context.dispatch_thread_id or "")
        surface = str(options.get("surface") or "cursor")
        from_agent = str(options.get("from_agent") or "continuity")
        execution_id = str(context.execution_id or "")

        outputs = getattr(context, "outputs", {}) or {}
        resolve_json = step_output_json(outputs, "resolve")

        if surface == "claude_ai":
            chat_url = resolve_json.get("chat_url") or options.get("chat_url")
            transcript_id = resolve_json.get("transcript_id") or options.get(
                "transcript_id"
            )
            if not chat_url or not transcript_id:
                payload = {
                    "refused": {
                        "code": "checkpoint.chat_url_required",
                        "message": "chat_url and transcript_id required for claude_ai seal",
                    }
                }
                return StepOutput(raw=json.dumps(payload), json=payload)
            payload = await seal_claude_ai(
                thread=thread,
                chat_url=str(chat_url),
                transcript_id=str(transcript_id),
                from_agent=from_agent,
                harvest_fn=cdp_ask_harvest,
                cortex_dispatch_fn=cortex_dispatch,
            )
            if payload.get("refused") is None:
                stargate_continuity_checkpoint_sealed(
                    execution_id=execution_id,
                    thread=thread,
                    surface=surface,
                    from_agent=from_agent,
                    transcript_id=str(payload.get("transcript_id") or ""),
                    turns_at_cp=int(payload.get("turn_count") or 0),
                    session_id=str(payload.get("session_id") or "") or None,
                )
            return StepOutput(raw=json.dumps(payload, default=str), json=payload)

        jsonl_path = resolve_json.get("jsonl_path") or options.get("jsonl_path")
        transcript_id = resolve_json.get("transcript_id") or options.get(
            "transcript_id"
        )
        if not jsonl_path:
            payload = {
                "refused": {
                    "code": "transcript_seal.missing_jsonl",
                    "message": "no jsonl_path",
                }
            }
            return StepOutput(raw=json.dumps(payload), json=payload)

        seal_args: dict[str, Any] = {
            "thread": thread,
            "jsonl_path": jsonl_path,
            "binding": "explicit_cp",
        }
        if transcript_id:
            seal_args["explicit_transcript_ids"] = [str(transcript_id)]

        seal = await cortex_dispatch("transcript_seal", seal_args)
        code = str(seal.get("code") or seal.get("reason") or "")

        if code == "transcript_seal.already_closed":
            session_id = str(seal.get("session_id") or "")
            turn_count = seal.get("turn_count")
            if turn_count is None and session_id:
                turn_count = _count_turns_from_journal(session_id)
            payload = {
                "session_id": session_id,
                "transcript_id": transcript_id or seal.get("conversation_uuid"),
                "turn_count": int(turn_count or 0),
                "already_closed": True,
                "refused": None,
                "messages_sha256": seal.get("content_hash"),
            }
        elif code in {"transcript_seal.not_lane_window", "transcript_seal.hollow"}:
            payload = {
                "refused": {
                    "code": code,
                    "message": str(seal.get("error") or code),
                },
                "already_closed": False,
            }
        elif seal.get("error"):
            refused_code = code or str(seal.get("reason") or "transcript_seal.refused")
            payload = {
                "refused": {
                    "code": refused_code,
                    "message": str(seal.get("error")),
                },
                "already_closed": False,
            }
        else:
            payload = {
                "session_id": seal.get("session_id"),
                "journal_row_id": seal.get("journal_row_id"),
                "transcript_id": transcript_id or seal.get("conversation_uuid"),
                "transcript_entity_id": seal.get("transcript_entity_id"),
                "turn_count": int(seal.get("turn_count") or 0),
                "messages_sha256": seal.get("content_hash"),
                "verbatim_codec": "messages-v1",
                "already_closed": False,
                "refused": None,
            }

        if payload.get("refused") is None:
            stargate_continuity_checkpoint_sealed(
                execution_id=execution_id,
                thread=thread,
                surface=surface,
                from_agent=from_agent,
                transcript_id=str(payload.get("transcript_id") or ""),
                turns_at_cp=int(payload.get("turn_count") or 0),
                session_id=str(payload.get("session_id") or "") or None,
            )

        return StepOutput(raw=json.dumps(payload, default=str), json=payload)
