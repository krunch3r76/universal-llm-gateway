"""Succession seal via the continuity_checkpoint cortex client."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, override

from continuity_tape.events import stargate_continuity_checkpoint_sealed
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import cortex_dispatch, refuse, step_output_json

_STUB = "succession seal (grokbot-checkpoint-v1)"


async def run_seal(
    options: dict[str, Any],
    envelope_step: dict[str, Any],
    *,
    execution_id: str,
    dispatch: Any = None,
) -> dict[str, Any]:
    post = dispatch or cortex_dispatch
    snapshot = str(envelope_step.get("snapshot_uri") or "")
    lane = str(options.get("root_thread") or options.get("verify_lane") or "")
    agent = str(options.get("bus_identity") or "")
    house = str(options.get("house") or "")
    n = options.get("n")
    msg_sha = str(envelope_step.get("messages_sha256") or "")
    session_id = "grokbot-" + hashlib.sha256(
        f"{house}|{n}|{msg_sha}".encode()
    ).hexdigest()[:20]
    close_args = {
        "session_id": session_id,
        "agent": agent,
        "session_summary_md": _STUB,
        "summary": _STUB,
        "transcript_messages_path": snapshot,
        "transcript_depth": "verbatim",
        "entity_ids": [f"agent-bus:{lane}"] if lane else [],
        "closed_by": "succession",
        "succession_seal_authority": True,
    }
    close = await post("session_close", close_args)
    if not isinstance(close, dict):
        return refuse("seal.transport", "session_close returned non-object")
    if close.get("error") and not close.get("already_closed"):
        return refuse(
            str(close.get("code") or "seal.refused"),
            str(close.get("error")),
        )
    session_id = str(close.get("session_id") or session_id)
    already = bool(close.get("already_closed"))
    proposals: list[str] = []
    echoed = close.get("meta") if isinstance(close.get("meta"), dict) else {}
    if envelope_step.get("provenance") and not echoed.get("provenance"):
        proposals.append(
            "session_close response omitted meta.provenance; cortex_store unchanged"
        )
    if isinstance(envelope_step.get("capture"), dict) and not echoed.get("capture"):
        proposals.append(
            "session_close response omitted meta.capture; cortex_store unchanged"
        )
    if not already:
        from cortex_store.dispatch_ops.ops_transcript_seal import (
            _stamp_succession_fields,
        )

        _stamp_succession_fields(
            session_id=session_id,
            sealed_by=agent,
            sealed_on=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            conversation_uuid=str(options.get("agent_id") or ""),
            dominant_lane=lane,
        )
    surface = str(envelope_step.get("surface") or "")
    stargate_continuity_checkpoint_sealed(
        execution_id=execution_id,
        thread=lane,
        surface=surface,
        from_agent=agent,
        transcript_id=str(options.get("agent_id") or ""),
        turns_at_cp=int(envelope_step.get("turn_count") or 0),
        session_id=session_id or None,
        coverage=str(envelope_step.get("coverage") or "") or None,
    )
    return {
        "refused": None,
        "session_id": session_id,
        "already_closed": already,
        "surface": surface,
        "messages_sha256": envelope_step.get("messages_sha256"),
        "provenance": envelope_step.get("provenance"),
        "producer": envelope_step.get("producer"),
        "proposals": proposals,
        "close_args": close_args,
        "sealed_on": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
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
