"""Event-catalog registration for ``mcp_events.record`` hop verb observability.

``services/mcp-server/tools/agent_bus/hop.py`` emits these via ``record()``; stubs
here exist so ``scripts/gen_event_catalog`` can document payload fields.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def AgentBusHopPosted(
    thread: str,
    reason: str,
    execution_id: str,
    work_key: str | None = None,
    caller_agent: str | None = None,
) -> Event:
    """Continuity hop CDP generate commissioned on the lane."""
    payload: dict[str, Any] = {
        "thread": thread,
        "reason": reason,
        "execution_id": execution_id,
    }
    if work_key is not None:
        payload["work_key"] = work_key
    if caller_agent is not None:
        payload["caller_agent"] = caller_agent
    return Event(
        signal="mcp.agentbus.hop.posted",
        payload=payload,
        scope="node",
        role="observation",
    )


@event_factory
def AgentBusHopRejected(
    reason: str,
    thread: str | None = None,
    work_key: str | None = None,
    caller_agent: str | None = None,
    model: str | None = None,
) -> Event:
    """Hop validation or continuity commission refused before or after relay."""
    payload: dict[str, Any] = {"reason": reason}
    if thread is not None:
        payload["thread"] = thread
    if work_key is not None:
        payload["work_key"] = work_key
    if caller_agent is not None:
        payload["caller_agent"] = caller_agent
    if model is not None:
        payload["model"] = model
    return Event(
        signal="mcp.agentbus.hop.rejected",
        payload=payload,
        scope="node",
        role="observation",
    )


__all__ = ["AgentBusHopPosted", "AgentBusHopRejected"]