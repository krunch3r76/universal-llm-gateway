"""Coordination events for the in-process agent-bus write ticket queue depth."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusWriteQueueArmed(  # noqa: N802
    depth: int,
    threshold: int,
) -> Event:
    """Signal: mcp.agentbus.write.queue.armed"""
    payload: dict[str, Any] = {"depth": depth, "threshold": threshold}
    return Event(
        signal="mcp.agentbus.write.queue.armed",
        payload=payload,
        role="coordination",
    )


@event_factory
def AgentBusWriteQueueCleared(  # noqa: N802
    depth: int,
    threshold: int,
) -> Event:
    """Signal: mcp.agentbus.write.queue.cleared"""
    payload: dict[str, Any] = {"depth": depth, "threshold": threshold}
    return Event(
        signal="mcp.agentbus.write.queue.cleared",
        payload=payload,
        role="coordination",
    )


def emit_write_queue_armed(*, depth: int, threshold: int) -> None:
    """Publish ``mcp.agentbus.write.queue.armed`` when depth exceeds threshold."""
    event = AgentBusWriteQueueArmed(depth=depth, threshold=threshold)
    _publish(event.signal, event.payload, role=event.role or "coordination")


def emit_write_queue_cleared(*, depth: int, threshold: int) -> None:
    """Publish ``mcp.agentbus.write.queue.cleared`` when depth is at or below threshold."""
    event = AgentBusWriteQueueCleared(depth=depth, threshold=threshold)
    _publish(event.signal, event.payload, role=event.role or "coordination")
