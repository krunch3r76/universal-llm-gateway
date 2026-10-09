"""Succession seal via the shared messages helper. No sqlite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, override

from continuity_tape.events import stargate_continuity_checkpoint_sealed
from continuity_tape.seal_messages import (
    derive_anchored_session_id,
    seal_messages_envelope,
)
from cortex_store.verbatim_succession import transcript_messages_path
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import cortex_dispatch, refuse, step_output_json

# Bus identity (grok-bot-hm1) is the close agent. The id prefix is the seat
# slug grok-bot. Recorded the same way as a:33286.
_SEAT = "grok-bot"


def _structural(options: dict[str, Any], envelope_step: dict[str, Any], opened_at: str) -> str:
    house = options.get("house")
    n = options.get("n")
    agent = options.get("bus_identity")
    prov = envelope_step.get("provenance")
    producer = envelope_step.get("producer")
    capture = envelope_step.get("capture")
    sha = envelope_step.get("messages_sha256")
    body = (
        f"House {house} checkpoint n={n} for agent {agent}. "
        f"Provenance {prov}. Producer {producer}. Capture {capture}. "
        f"messages_sha256 {sha}. Conversation start {opened_at}. "
        "Succession seal (grokbot-checkpoint-v1) from the pour envelope. "
        "The structural layer names this house so a two-turn harvest still "
        "clears the composed-transcript minimum on its own."
    )
    return f"## Session Summary\n\n{body}\n"


def _opened_at(options: dict[str, Any], envelope: dict[str, Any]) -> str | None:
    messages = envelope.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            for key in ("timestamp", "created_at", "ts"):
                value = message.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    meta = envelope.get("meta") if isinstance(envelope.get("meta"), dict) else {}
    for key in ("opened_at", "started_at"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("opened_at", "house_opened_at"):
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _prior_messages_sha(session_id: str) -> str | None:
    from cortex_store.dispatch_ops._shared import _FILES_ROOT

    rel = transcript_messages_path(f"notes/system/transcripts/{session_id}.md")
    path = Path(_FILES_ROOT) / rel
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    meta = payload.get("meta") if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        return None
    sha = meta.get("messages_sha256")
    return str(sha) if isinstance(sha, str) and sha else None


async def run_seal(
    options: dict[str, Any],
    envelope_step: dict[str, Any],
    *,
    execution_id: str,
    dispatch: Any = None,
) -> dict[str, Any]:
    if envelope_step.get("_skipped") or envelope_step.get("refused") or not envelope_step.get(
        "snapshot_uri"
    ):
        return refuse("seal.upstream_skipped", "envelope did not produce a snapshot")
    snapshot = str(envelope_step.get("snapshot_uri") or "")
    if "/threads/" in snapshot.replace("\\", "/"):
        return refuse("seal.path_rejected", "threads path is not a seal target")
    post = dispatch or cortex_dispatch
    envelope = envelope_step.get("envelope")
    if not isinstance(envelope, dict):
        return refuse("seal.envelope_missing", "envelope step did not carry messages")
    opened = _opened_at(options, envelope)
    if not opened:
        return refuse("seal.missing_start", "conversation start is absent")
    agent = str(options.get("bus_identity") or "")
    agent_id = str(options.get("agent_id") or "")
    lane = str(options.get("root_thread") or options.get("verify_lane") or "")
    structural = _structural(options, envelope_step, opened)
    # Optional identity read. A failure keeps the derived id (test 9).
    try:
        await post(
            "entity_get",
            {"entity_id": f"journal:{agent_id}", "intent": "card"},
        )
    except Exception:
        pass
    msg_sha = str(envelope_step.get("messages_sha256") or "")
    # Read the authority seal before close. An unchanged rerun already has this sha.
    preview_id = ""
    preview_id = derive_anchored_session_id(_SEAT, opened, agent_id or None)
    prior = _prior_messages_sha(preview_id)
    result = await seal_messages_envelope(
        envelope,
        agent=agent,
        seat_slug=_SEAT,
        conversation_uuid=agent_id or None,
        opened_at=opened,
        thread=lane,
        structural_md=structural,
        summary=structural.replace("\n", " ")[:500],
        cortex_dispatch_fn=post,
        attach_conversation_uuid=True,
    )
    if result.refused:
        return {
            "refused": result.refused,
            "session_id": result.session_id or None,
            "already_closed": False,
            "close_args": result.close_args,
        }
    session_id = result.session_id
    close = result.close or {}
    extended = prior != msg_sha
    if extended:
        surface = str(envelope_step.get("surface") or "")
        stargate_continuity_checkpoint_sealed(
            execution_id=execution_id,
            thread=lane,
            surface=surface,
            from_agent=agent,
            transcript_id=agent_id,
            turns_at_cp=int(envelope_step.get("turn_count") or 0),
            session_id=session_id or None,
            coverage=str(envelope_step.get("coverage") or "") or None,
        )
    return {
        "refused": None,
        "session_id": session_id,
        "journal_row_id": close.get("journal_row_id"),
        "already_closed": bool(close.get("already_closed")),
        "surface": str(envelope_step.get("surface") or ""),
        "messages_sha256": envelope_step.get("messages_sha256"),
        "provenance": envelope_step.get("provenance"),
        "producer": envelope_step.get("producer"),
        "proposals": [],
        "close_args": result.close_args,
    }


class GrokbotCheckpointSealHandler(BaseHandler):
    step_type = "grokbot_checkpoint_seal_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        envelope_step = step_output_json(
            getattr(context, "outputs", {}) or {}, "envelope"
        )
        payload = await run_seal(
            options,
            envelope_step,
            execution_id=str(getattr(context, "execution_id", "") or ""),
        )
        return StepOutput(raw="", json=payload)
