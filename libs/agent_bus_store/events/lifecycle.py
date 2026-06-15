"""Event factories and emit helpers for agent-bus thread lifecycle signals.

All four signals are defined here even though watchdog steps (5-6) are what
consume the Abandoned factory and the watchdog_reap trigger / reason values.
Defining them now prevents the watchdog ticket from amending event contracts
as a side effect.
"""

from __future__ import annotations

from typing import Literal

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish  # noqa: I001

# ── Factories ────────────────────────────────────────────────────────────────


@event_factory
def AgentBusThreadLifecycleTransitioned(  # noqa: N802
    thread: str,
    from_state: str | None,
    to_state: str,
    trigger: Literal[
        "create",
        "admit",
        "close",
        "close_on_read",
        "turn_posted",
        "delivery_sent",
        "delivery_failed",
        "watchdog_reap",
        "reopen",
    ],
) -> Event:
    """Signal: mcp.agentbus.thread.lifecycle.transitioned"""
    return Event(
        signal="mcp.agentbus.thread.lifecycle.transitioned",
        payload={
            "thread": thread,
            "from_state": from_state,
            "to_state": to_state,
            "trigger": trigger,
        },
        role="coordination",
    )


@event_factory
def AgentBusThreadAbandoned(  # noqa: N802
    thread: str,
    reason: Literal[
        "pending_ttl_exceeded",
        "admitted_ttl_exceeded",
        "all_terminal_no_delivery",
        "tracker_expired",
    ],
    link_count: int,
    terminal_count: int,
    delivered_count: int,
) -> Event:
    """Signal: mcp.agentbus.thread.abandoned"""
    return Event(
        signal="mcp.agentbus.thread.abandoned",
        payload={
            "thread": thread,
            "reason": reason,
            "link_count": link_count,
            "terminal_count": terminal_count,
            "delivered_count": delivered_count,
        },
    )


@event_factory
def AgentBusDispatchAdmitFailed(  # noqa: N802
    execution_id: str,
    thread: str,
    status_code: int,
    error_preview: str,
) -> Event:
    """Signal: mcp.agentbus.dispatch.admit.failed"""
    return Event(
        signal="mcp.agentbus.dispatch.admit.failed",
        payload={
            "execution_id": execution_id,
            "thread": thread,
            "status_code": status_code,
            "error_preview": error_preview,
        },
    )


@event_factory
def AgentBusThreadReopened(  # noqa: N802
    thread: str,
    from_state: str,
    to_state: str,
) -> Event:
    """Signal: mcp.agentbus.thread.reopened

    Emitted when a turn POST re-opens a previously-terminal thread
    (completed, abandoned, or failed).
    """
    return Event(
        signal="mcp.agentbus.thread.reopened",
        payload={
            "thread": thread,
            "from_state": from_state,
            "to_state": to_state,
        },
    )


# ── Emit helpers (called from db/lifecycle.py) ───────────────────────────────


def emit_lifecycle_transitioned(
    thread: str,
    from_state: str | None,
    to_state: str,
    trigger: Literal[
        "create",
        "admit",
        "close",
        "turn_posted",
        "delivery_sent",
        "delivery_failed",
        "watchdog_reap",
        "reopen",
    ],
) -> None:
    """Build and publish a lifecycle.transitioned event."""
    event = AgentBusThreadLifecycleTransitioned(
        thread=thread,
        from_state=from_state,
        to_state=to_state,
        trigger=trigger,
    )
    _publish(event.signal, event.payload, role=event.role)


def emit_thread_reopened(thread: str, from_state: str, to_state: str) -> None:
    """Build and publish a thread.reopened event."""
    event = AgentBusThreadReopened(
        thread=thread, from_state=from_state, to_state=to_state
    )
    _publish(event.signal, event.payload)


def emit_dispatch_admit_failed(
    execution_id: str, thread: str, status_code: int, error_preview: str
) -> None:
    """Build and publish a dispatch.admit.failed event."""
    event = AgentBusDispatchAdmitFailed(
        execution_id=execution_id,
        thread=thread,
        status_code=status_code,
        error_preview=error_preview,
    )
    _publish(event.signal, event.payload)


def emit_thread_abandoned(
    thread: str,
    reason: Literal[
        "pending_ttl_exceeded",
        "admitted_ttl_exceeded",
        "all_terminal_no_delivery",
        "tracker_expired",
    ],
    link_count: int,
    terminal_count: int,
    delivered_count: int,
) -> None:
    """Build and publish a thread.abandoned event."""
    event = AgentBusThreadAbandoned(
        thread=thread,
        reason=reason,
        link_count=link_count,
        terminal_count=terminal_count,
        delivered_count=delivered_count,
    )
    _publish(event.signal, event.payload)


@event_factory
def AgentBusWatchdogSweepFailed(error: str) -> Event:  # noqa: N802
    """Signal: mcp.agentbus.watchdog.sweep.failed"""
    return Event(
        signal="mcp.agentbus.watchdog.sweep.failed",
        payload={"error": error},
    )


def emit_watchdog_sweep_failed(error: str) -> None:
    """Build and publish a watchdog.sweep.failed event."""
    event = AgentBusWatchdogSweepFailed(error=error)
    _publish(event.signal, event.payload)


@event_factory
def AgentBusDispatchOrphaned(  # noqa: N802
    execution_id: str,
    thread_id: str,
    pipeline_id: str,
    linked_at: str,
    age_s: float,
) -> Event:
    """Signal: mcp.agentbus.dispatch.orphaned"""
    return Event(
        signal="mcp.agentbus.dispatch.orphaned",
        payload={
            "execution_id": execution_id,
            "thread_id": thread_id,
            "pipeline_id": pipeline_id,
            "linked_at": linked_at,
            "age_s": age_s,
        },
    )


def emit_dispatch_orphaned(
    *,
    execution_id: str,
    thread_id: str,
    pipeline_id: str,
    linked_at: str,
    age_s: float,
) -> None:
    """Build and publish a dispatch.orphaned event."""
    event = AgentBusDispatchOrphaned(
        execution_id=execution_id,
        thread_id=thread_id,
        pipeline_id=pipeline_id,
        linked_at=linked_at,
        age_s=age_s,
    )
    _publish(event.signal, event.payload)
