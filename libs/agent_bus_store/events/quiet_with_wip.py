"""Event factory for quiet-with-WIP soft-actuate (A′ — arc 6885).

Publishes ``mcp.agentbus.quiet_with_wip.fired`` when the bus watchdog sweep
detects seat silence with open WIP; pairs with the durable alarm row and lane turn.
"""

from __future__ import annotations

from typing import Literal

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusQuietWithWipFired(  # noqa: N802
    thread: str,
    seat: str,
    reason: Literal["wip_in_flight", "closeout_unharvested", "pickup_unbound"],
    alarm_id: str,
    wip_execution_ids: list[str],
) -> Event:
    """Signal: mcp.agentbus.quiet_with_wip.fired

    Soft-actuate companion to the watchdog quiet sweep — Event Service queryable
    and paired with a lane turn the WAKE relay can consume.
    """
    return Event(
        signal="mcp.agentbus.quiet_with_wip.fired",
        payload={
            "thread": thread,
            "seat": seat,
            "reason": reason,
            "alarm_id": alarm_id,
            "wip_execution_ids": wip_execution_ids,
        },
        role="coordination",
    )


def emit_quiet_with_wip_fired(
    *,
    thread: str,
    seat: str,
    reason: Literal["wip_in_flight", "closeout_unharvested", "pickup_unbound"],
    alarm_id: str,
    wip_execution_ids: list[str],
) -> None:
    """Build and publish a quiet_with_wip.fired event to the Event Service."""
    event = AgentBusQuietWithWipFired(
        thread=thread,
        seat=seat,
        reason=reason,
        alarm_id=alarm_id,
        wip_execution_ids=wip_execution_ids,
    )
    _publish(event.signal, event.payload, role=event.role)
