"""Emit helper for successful lane↔branch association appends."""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusBranchAssociated(  # noqa: N802
    thread_id: str,
    branch_name: str,
    association_id: int,
) -> Event:
    """Signal: mcp.agentbus.branch.associated"""
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "branch_name": branch_name,
        "association_id": association_id,
    }
    return Event(
        signal="mcp.agentbus.branch.associated",
        payload=payload,
        role="observation",
    )


def emit_branch_associated(
    *,
    thread_id: str,
    branch_name: str,
    association_id: int,
) -> None:
    """Publish advisory event after a successful association append."""
    event = AgentBusBranchAssociated(
        thread_id=thread_id,
        branch_name=branch_name,
        association_id=association_id,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")
