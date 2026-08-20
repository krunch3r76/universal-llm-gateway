"""Observation emit for a newly appended CSE session-address association.

Mirrors ``lane_bound``: publish after a successful insert, never on a no-op
or refused registration-only bind.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusThreadCseBound(  # noqa: N802
    thread_id: str,
    cse_chat_url: str,
    cse_registration_id: str | None,
    association_id: int,
    prior_association_id: int | None,
    bound_by: str | None,
) -> Event:
    """Signal: mcp.agentbus.thread.cse.bound"""
    payload: dict[str, Any] = {
        "thread_id": thread_id,
        "cse_chat_url": cse_chat_url,
        "cse_registration_id": cse_registration_id,
        "association_id": association_id,
        "prior_association_id": prior_association_id,
        "bound_by": bound_by,
    }
    return Event(
        signal="mcp.agentbus.thread.cse.bound",
        payload=payload,
        role="observation",
    )


def emit_cse_bound(
    *,
    thread_id: str,
    cse_chat_url: str,
    cse_registration_id: str | None,
    association_id: int,
    prior_association_id: int | None,
    bound_by: str | None,
) -> None:
    """Publish the observation event after a CSE association row is inserted."""
    event = AgentBusThreadCseBound(
        thread_id=thread_id,
        cse_chat_url=cse_chat_url,
        cse_registration_id=cse_registration_id,
        association_id=association_id,
        prior_association_id=prior_association_id,
        bound_by=bound_by,
    )
    _publish(event.signal, event.payload, role=event.role or "observation")
