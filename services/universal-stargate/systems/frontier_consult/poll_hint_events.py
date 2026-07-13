"""Poll-hint issuance signals for handoff/generate admit paths."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from universal_event_bus import Event, event_factory

from .cursor_sdk_generate_signals import publish_frontier_event


@event_factory
def FrontierPollHintIssued(  # noqa: N802
    request_id: str,
    thread_id: str,
    caller_agent: str | None,
    wait_seconds: int,
    after_turn: int,
    reply_from_agent: str,
    issued_at: str,
) -> Event:
    """Admit returned poll_hint — correlates with mcp.agentbus.wait.called."""
    return Event(
        signal="frontier.poll.hint.issued",
        payload={
            "request_id": request_id,
            "thread_id": thread_id,
            "caller_agent": caller_agent,
            "wait_seconds": wait_seconds,
            "after_turn": after_turn,
            "reply_from_agent": reply_from_agent,
            "issued_at": issued_at,
        },
        scope="node",
    )


def emit_poll_hint_issued(
    *,
    request_id: str,
    thread_id: str,
    caller_agent: str | None,
    wait_seconds: int,
    after_turn: int,
    reply_from_agent: str,
    issued_at: str | None = None,
) -> None:
    ts = issued_at or datetime.now(UTC).isoformat()
    publish_frontier_event(
        FrontierPollHintIssued(
            request_id=request_id,
            thread_id=thread_id,
            caller_agent=caller_agent,
            wait_seconds=wait_seconds,
            after_turn=after_turn,
            reply_from_agent=reply_from_agent,
            issued_at=ts,
        )
    )


def emit_poll_hint_from_handoff(
    *,
    request_id: str,
    thread_id: str,
    caller_agent: str | None,
    handoff_fields: dict[str, Any],
) -> None:
    """Emit ``frontier.poll.hint.issued`` from ``build_handoff_result`` fields."""
    poll_hint = handoff_fields.get("poll_hint")
    if not isinstance(poll_hint, dict):
        return
    arguments = poll_hint.get("arguments")
    if not isinstance(arguments, dict):
        return
    reply_from = handoff_fields.get("reply_from_agent") or arguments.get("from_agent")
    if not reply_from:
        return
    emit_poll_hint_issued(
        request_id=request_id,
        thread_id=thread_id,
        caller_agent=caller_agent,
        wait_seconds=int(arguments.get("wait_seconds", 60)),
        after_turn=int(arguments.get("after_turn", 1)),
        reply_from_agent=str(reply_from),
    )
