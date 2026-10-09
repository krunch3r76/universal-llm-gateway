"""Succession seal via the continuity_checkpoint cortex client."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, override

from agent_seat.session_id import derive_session_id_from_timestamp
from continuity_tape.events import stargate_continuity_checkpoint_sealed
from cortex_store.session_close_validation import _validate_session_close_args
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import cortex_dispatch, refuse, step_output_json

_STUB = (
    "succession seal (grokbot-checkpoint-v1). "
    "Grok bot house checkpoint sealed from the pour envelope."
)
_SEAT = "grok-bot"
# Stable clock so a rerun before the journal row exists still mints the same id.
_STABLE_STAMP = "2026-01-01T00:00:00Z"


class SealLookupError(Exception):
    """``cortex_conn`` failed while reading this agent's journal row."""

    def __init__(self, cause: BaseException) -> None:
        self.cause = cause
        super().__init__(f"{type(cause).__name__}: {cause}")


def _journal_seal_state(conversation_uuid: str) -> tuple[str | None, str | None]:
    """Latest journal id and the transcript entity's sealed messages hash.

    Identity is ``conversation_uuid`` (the grok ``agent_id``). The hash lives
    on ``transcript:{session_id}`` attributes ``messages_sha256``, written by
    persist at seal time. A missing row is ``(None, None)``, not an error.
    """
    from cortex_store.db import cortex_conn

    try:
        conn = cortex_conn()
        try:
            row = conn.execute(
                "SELECT session_id FROM session_journals WHERE conversation_uuid = ? "
                "ORDER BY id DESC LIMIT 1",
                (conversation_uuid,),
            ).fetchone()
            if not row or not row["session_id"]:
                return None, None
            session_id = str(row["session_id"])
            entity = conn.execute(
                "SELECT attributes FROM entities WHERE id = ?",
                (f"transcript:{session_id}",),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        raise SealLookupError(exc) from exc
    sealed_sha: str | None = None
    if entity and entity["attributes"]:
        try:
            attrs = json.loads(entity["attributes"])
        except json.JSONDecodeError:
            attrs = {}
        raw_sha = attrs.get("messages_sha256") if isinstance(attrs, dict) else None
        if isinstance(raw_sha, str) and raw_sha:
            sealed_sha = raw_sha
    return session_id, sealed_sha


def _lookup_session_id_any_closed_by(conversation_uuid: str) -> str | None:
    """Latest session_journals row for this uuid, any closed_by."""
    session_id, _sealed_sha = _journal_seal_state(conversation_uuid)
    return session_id


def _stable_session_id(house: str, n: Any, msg_sha: str, agent_id: str) -> str:
    """Same id for one agent conversation, whatever the snapshot size is.

    ``n`` and ``msg_sha`` are accepted so callers can show they do not change
    the id. The material is ``agent_id|house`` only, matching the lookup key
    ``conversation_uuid=agent_id`` once that row exists.
    """
    del n, msg_sha
    return derive_session_id_from_timestamp(
        _SEAT,
        _STABLE_STAMP,
        deterministic=True,
        conversation_uuid=f"{agent_id}|{house}",
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
    sealed_sha: str | None = None
    if agent_id:
        try:
            session_id, sealed_sha = _journal_seal_state(agent_id)
            session_id = session_id or ""
        except SealLookupError as exc:
            return refuse("seal.lookup_failed", str(exc))
    if not session_id:
        session_id = _stable_session_id(house, n, msg_sha, agent_id)
    if sealed_sha and msg_sha and sealed_sha == msg_sha:
        return {
            "refused": None,
            "session_id": session_id,
            "already_closed": True,
            "surface": str(envelope_step.get("surface") or ""),
            "messages_sha256": envelope_step.get("messages_sha256"),
            "provenance": envelope_step.get("provenance"),
            "producer": envelope_step.get("producer"),
            "proposals": [],
            "close_args": None,
            "sealed_on": sealed_on,
        }
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
