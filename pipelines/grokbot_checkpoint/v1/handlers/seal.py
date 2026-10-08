"""Succession seal via the continuity_checkpoint cortex client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, override

from agent_seat.session_id import derive_session_id_from_timestamp
from continuity_tape.events import stargate_continuity_checkpoint_sealed
from cortex_store.session_close_validation import _validate_session_close_args
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
    if envelope_step.get("_skipped") or envelope_step.get("refused") or not envelope_step.get(
        "snapshot_uri"
    ):
        return refuse("seal.upstream_skipped", "envelope did not produce a snapshot")
    post = dispatch or cortex_dispatch
    snapshot = str(envelope_step.get("snapshot_uri") or "")
    lane = str(options.get("root_thread") or options.get("verify_lane") or "")
    agent = str(options.get("bus_identity") or "")
    house = str(options.get("house") or "")
    n = options.get("n")
    msg_sha = str(envelope_step.get("messages_sha256") or "")
    sealed_on = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    agent_id = str(options.get("agent_id") or "")
    session_id = ""
    if agent_id:
        try:
            from cortex_store.session_close_successor_hop import (
                lookup_journaled_by_conversation_uuid,
            )

            found = lookup_journaled_by_conversation_uuid(agent_id)
        except Exception:
            found = None
        if found is not None and getattr(found, "session_id", None):
            session_id = str(found.session_id)
    if not session_id:
        session_id = derive_session_id_from_timestamp(
            agent,
            sealed_on,
            deterministic=True,
            conversation_uuid=agent_id or f"{house}|{n}|{msg_sha}",
        )
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
    rejected = _validate_session_close_args(
        session_id=session_id,
        agent=agent,
        transcript_jsonl_path=None,
        session_summary_md=_STUB,
        summary=_STUB,
        transcript_messages_path=snapshot,
        transcript_depth="verbatim",
        emit_rejected=False,
    )
    if isinstance(rejected, dict) and rejected.get("error"):
        return refuse(str(rejected.get("reason") or "seal.invalid"), str(rejected.get("error")))
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
            sealed_on=sealed_on,
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
        "sealed_on": sealed_on,
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
