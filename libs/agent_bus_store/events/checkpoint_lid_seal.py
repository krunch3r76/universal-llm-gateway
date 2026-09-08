"""Emit helpers for O15 D2 lid-close seal observation events."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusCheckpointLidSealRequested(  # noqa: N802
    thread: str,
    transcript_id: str,
    turns_at_cp: int,
) -> Event:
    """Signal: agent_bus.checkpoint.lid_seal_requested"""
    return Event(
        signal="agent_bus.checkpoint.lid_seal_requested",
        payload={
            "thread": thread,
            "transcript_id": transcript_id,
            "turns_at_cp": turns_at_cp,
        },
        role="observation",
    )


@event_factory
def AgentBusCheckpointLidSealFailed(  # noqa: N802
    thread: str,
    transcript_id: str,
    turns_at_cp: int,
    error: str,
    detail: Any | None = None,
) -> Event:
    """Signal: agent_bus.checkpoint.lid_seal_failed"""
    payload: dict[str, Any] = {
        "thread": thread,
        "transcript_id": transcript_id,
        "turns_at_cp": turns_at_cp,
        "error": error,
    }
    if detail is not None:
        payload["detail"] = detail
    return Event(
        signal="agent_bus.checkpoint.lid_seal_failed",
        payload=payload,
        role="observation",
    )


def emit_checkpoint_lid_seal_requested(
    *,
    thread: str,
    transcript_id: str,
    turns_at_cp: int,
) -> None:
    """Publish ``agent_bus.checkpoint.lid_seal_requested`` before harvest."""
    event = AgentBusCheckpointLidSealRequested(
        thread=thread,
        transcript_id=transcript_id,
        turns_at_cp=turns_at_cp,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")


def emit_checkpoint_lid_seal_failed(
    *,
    thread: str,
    transcript_id: str,
    turns_at_cp: int,
    error: str,
    detail: Any | None = None,
) -> None:
    """Publish ``agent_bus.checkpoint.lid_seal_failed`` when harvest fails."""
    event = AgentBusCheckpointLidSealFailed(
        thread=thread,
        transcript_id=transcript_id,
        turns_at_cp=turns_at_cp,
        error=error,
        detail=detail,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")


__all__ = [
    "emit_checkpoint_lid_seal_failed",
    "emit_checkpoint_lid_seal_requested",
]
