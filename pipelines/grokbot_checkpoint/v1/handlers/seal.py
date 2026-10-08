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
_SEAT = "grok-bot"
# Stable clock so a rerun before the journal row exists still mints the same id.
_STABLE_STAMP = "2026-01-01T00:00:00Z"


def _lookup_session_id_any_closed_by(conversation_uuid: str) -> str | None:
    """Latest session_journals row for this uuid, any closed_by.

    ``lookup_journaled_by_conversation_uuid`` skips succession rows, so a grok
    seal would never see itself. This matches ``_lookup_session_id_for_transcript``
    in seal_claude_ai.py. A human-closed row is reused too: session_close then
    returns already_closed and this pipeline does not mint a second seal.
    """
    from cortex_store.db import cortex_conn

    conn = cortex_conn()
    try:
        row = conn.execute(
            "SELECT session_id FROM session_journals WHERE conversation_uuid = ? "
            "ORDER BY id DESC LIMIT 1",
            (conversation_uuid,),
        ).fetchone()
    finally:
        conn.close()
    if row and row["session_id"]:
        return str(row["session_id"])
    return None


def _stable_session_id(house: str, n: Any, msg_sha: str, agent_id: str) -> str:
    return derive_session_id_from_timestamp(
        _SEAT,
        _STABLE_STAMP,
        deterministic=True,
        conversation_uuid=f"{agent_id}|{house}|{n}|{msg_sha}",
    )


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
            session_id = _lookup_session_id_any_closed_by(agent_id) or ""
        except Exception:
            session_id = ""
    if not session_id:
        session_id = _stable_session_id(house, n, msg_sha, agent_id)
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
            str(close.get("reason") or close.get("code") or "seal.refused"),
            str(close.get("error")),
        )
    session_id = str(close.get("session_id") or session_id)
    already = bool(close.get("already_closed"))
    proposals: list[str] = []
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
