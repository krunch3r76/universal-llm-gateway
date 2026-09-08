"""Emit helper for CHECKPOINT in-flight producer projection observation."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusCheckpointProducersProjected(  # noqa: N802
    thread: str,
    producer_count: int,
    execution_ids: list[str],
) -> Event:
    """Signal: agent_bus.checkpoint.producers_projected"""
    payload: dict[str, Any] = {
        "thread": thread,
        "producer_count": producer_count,
        "execution_ids": execution_ids,
    }
    return Event(
        signal="agent_bus.checkpoint.producers_projected",
        payload=payload,
        role="observation",
    )


def emit_checkpoint_producers_projected(
    *,
    thread: str,
    producer_count: int,
    execution_ids: list[str],
) -> None:
    """Publish ``agent_bus.checkpoint.producers_projected`` after CHECKPOINT post."""
    event = AgentBusCheckpointProducersProjected(
        thread=thread,
        producer_count=producer_count,
        execution_ids=execution_ids,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")


__all__ = ["emit_checkpoint_producers_projected"]
