"""Succession seal via the shared messages helper. No files-root read."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, override

from continuity_tape.events import stargate_continuity_checkpoint_sealed
from continuity_tape.seal_messages import (
    derive_anchored_session_id,
    seal_messages_envelope,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import cortex_dispatch, refuse, step_output_json

# Bus identity (grok-bot-hm1) is the close agent. The id prefix is the seat
# slug grok-bot. Recorded the same way as a:33286.
_SEAT = "grok-bot"


def _structural(
    options: dict[str, Any], envelope_step: dict[str, Any], opened_at: str
) -> str:
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


def _normalise_utc(raw: str) -> str | None:
    """Parse one ISO instant and return ``YYYY-MM-DDTHH:MM:SSZ``.

    A trailing ``Z`` is accepted. Naive or non-ISO text returns None so the
    caller refuses before any close. Offset forms of the same instant share
    one UTC string, so preview and close cannot mint different ids.
    """
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_message_timestamp(envelope: dict[str, Any]) -> str | None:
    messages = envelope.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    message = messages[0]
    if not isinstance(message, dict):
        return None
    for key in ("timestamp", "created_at", "ts"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _raw_opened_at(options: dict[str, Any], envelope: dict[str, Any]) -> str | None:
    """meta.opened_at, then meta.started_at, else the first message, else options."""
    meta = envelope.get("meta") if isinstance(envelope.get("meta"), dict) else {}
    for key in ("opened_at", "started_at"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    first = _first_message_timestamp(envelope)
    if first:
        return first
    for key in ("opened_at", "house_opened_at"):
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _opened_at(options: dict[str, Any], envelope: dict[str, Any]) -> str | None:
    raw = _raw_opened_at(options, envelope)
    if raw is None:
        return None
    return _normalise_utc(raw)


def _entity_missing(value: object) -> bool:
    """True for a cortex 404. The live detail is ``Entity entity not found``."""
    status_code = None
    if isinstance(value, dict):
        status_code = value.get("status_code")
        value = value.get("error", value)
    if status_code == 404:
        return True
    text = str(value)
    return "not found" in text.lower() and "ambiguous" not in text.lower()


async def _prior_messages_sha(
    session_id: str, dispatch: Any
) -> tuple[str | None, str | None]:
    """Prior messages hash via cortex ``entity_get``, not a SQLite connection.

    Returns ``(sha, None)`` when the read succeeds. A missing entity is
    ``(None, None)`` (first seal). ``(None, "prior_read_failed")`` means the
    read failed; the caller must not treat that as a new extension.
    """
    try:
        body = await dispatch(
            "entity_get",
            {"entity_id": f"transcript:{session_id}", "intent": "full"},
        )
    except Exception as exc:  # noqa: BLE001 — HTTP hop failure is a skipped event
        if _entity_missing(exc):
            return None, None
        return None, "prior_read_failed"
    if not isinstance(body, dict):
        return None, "prior_read_failed"
    if "error" in body or "status_code" in body or "detail" in body:
        if _entity_missing(body):
            return None, None
        return None, "prior_read_failed"
    if "id" not in body and "attributes" not in body:
        return None, "prior_read_failed"
    attrs = body.get("attributes")
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except json.JSONDecodeError:
            return None, "prior_read_failed"
    if attrs is None:
        return None, None
    if not isinstance(attrs, dict):
        return None, "prior_read_failed"
    sha = attrs.get("messages_sha256")
    return (str(sha) if isinstance(sha, str) and sha else None), None


async def run_seal(
    options: dict[str, Any],
    envelope_step: dict[str, Any],
    *,
    execution_id: str,
    dispatch: Any = None,
) -> dict[str, Any]:
    if (
        envelope_step.get("_skipped")
        or envelope_step.get("refused")
        or not envelope_step.get("snapshot_uri")
    ):
        return refuse("seal.upstream_skipped", "envelope did not produce a snapshot")
    snapshot = str(envelope_step.get("snapshot_uri") or "")
    if "/threads/" in snapshot.replace("\\", "/"):
        return refuse("seal.path_rejected", "threads path is not a seal target")
    post = dispatch or cortex_dispatch
    envelope = envelope_step.get("envelope")
    if not isinstance(envelope, dict):
        return refuse("seal.envelope_missing", "envelope step did not carry messages")
    raw_opened = _raw_opened_at(options, envelope)
    if not raw_opened:
        return refuse("seal.missing_start", "conversation start is absent")
    opened = _normalise_utc(raw_opened)
    if not opened:
        return refuse(
            "seal.start_unparseable", "conversation start is not an ISO instant"
        )
    agent = str(options.get("bus_identity") or "")
    agent_id = str(options.get("agent_id") or "").strip()
    lane = str(options.get("root_thread") or options.get("verify_lane") or "")
    structural = _structural(options, envelope_step, opened)
    msg_sha = str(envelope_step.get("messages_sha256") or "")
    preview_id = derive_anchored_session_id(_SEAT, opened, agent_id or None)
    prior, prior_err = await _prior_messages_sha(preview_id, post)
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
    # A failed prior read is not "no prior": skip the event instead of re-firing.
    extended = prior_err is None and prior != msg_sha
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
        "event_suppressed_reason": prior_err,
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
