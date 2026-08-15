"""Emit helpers for thread-close observation events.

``mcp.agentbus.thread.closed`` — every successful status→closed transition
(store ``close_thread`` / ``update_thread``), so CLI and direct HTTP closes are
observable without relying on the MCP tool layer.

``manage.charter.tick.root_closed`` — when the reserved ``charter-runner``
enrollment tag leaves a closed root (unenroll-after-close, or close of an
still-enrolled root). Dispatch-monitor folds this as the sole authority that
closes a charter root — without it the board parks forever after the worker
leg goes terminal.
"""

from __future__ import annotations

from typing import Any

from universal_event_bus import Event, event_factory

from .publisher import emit as _publish


@event_factory
def AgentBusThreadClosed(  # noqa: N802
    thread: str,
    via: str = "",
) -> Event:
    """Signal: mcp.agentbus.thread.closed

    ``via`` empty ⇒ omitted from payload (plain /close or equivalent).
    Non-empty values match the event-contracts vocabulary (``update_thread``,
    ``reply``, ``ephemeral_delivery``, ``watchdog_reaper``, …).
    """
    payload: dict[str, Any] = {"thread": thread}
    if via:
        payload["via"] = via
    return Event(
        signal="mcp.agentbus.thread.closed",
        payload=payload,
        role="observation",
    )


def emit_thread_closed(thread: str, *, via: str | None = None) -> None:
    """Publish ``mcp.agentbus.thread.closed`` for a completed close."""
    event = AgentBusThreadClosed(thread=thread, via=via or "")
    _publish(event.signal, event.payload, role=event.role or "observation")


def emit_persistent_thread_closed(
    thread: str,
    *,
    via: str | None = None,
) -> None:
    """Publish the advisory close signal for a persistent bus thread."""
    payload: dict[str, Any] = {"thread": thread}
    if via:
        payload["via"] = via
    _publish(
        "mcp.agentbus.persistent_thread.closed",
        payload,
        role="observation",
    )


def emit_charter_root_closed_on_unenroll(
    *,
    root: str,
    reason: str = "unenroll_after_close",
    checkpoint_turn: int | None = None,
) -> None:
    """Publish ``manage.charter.tick.root_closed`` after stripping enrollment.

    Payload shape matches ``emit_manage_charter_tick_root_closed`` so the
    dispatch-monitor charter fold treats store-path unenrolls like tick
    state-close.
    """
    payload: dict[str, Any] = {
        "root": root,
        "reason": reason,
        "checkpoint_turn": checkpoint_turn,
        "closed": True,
        "unenrolled": True,
    }
    _publish(
        "manage.charter.tick.root_closed",
        payload,
        role="observation",
    )


def enrollment_stripped(
    *,
    prior_tags: list[str],
    new_tags: list[str],
) -> bool:
    """True when ``charter-runner`` was present and is absent after the write."""
    from agent_bus_store.enrollment_guard import ENROLLMENT_TAG

    return ENROLLMENT_TAG in prior_tags and ENROLLMENT_TAG not in new_tags


def maybe_emit_charter_root_closed_on_unenroll(
    *,
    root: str,
    prior_tags: list[str],
    new_tags: list[str],
    status: str,
    reason: str = "unenroll_after_close",
    checkpoint_turn: int | None = None,
) -> bool:
    """Emit ``root_closed`` iff enrollment left a closed root. Returns emitted."""
    if str(status or "") != "closed":
        return False
    if not enrollment_stripped(prior_tags=prior_tags, new_tags=new_tags):
        return False
    emit_charter_root_closed_on_unenroll(
        root=root,
        reason=reason,
        checkpoint_turn=checkpoint_turn,
    )
    return True
