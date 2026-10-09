"""Observation events for the public MCP edge probe and listener silence.

The manage edge-probe loop is the only caller. Factories build the event;
the loop publishes it on the manage observation socket with ``source=manage``.
These factories do not publish, so a unit test can construct one without a
socket. Role is observation: suppressing them loses a monitor, not a state
machine. Scope is node: the vantage is this host's DNS and hairpin.
"""

from __future__ import annotations

from universal_event_bus.events import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def mcp_edge_probe_succeeded(*, host: str, ip: str, http_status: int) -> Event:
    """Public-vantage ``/health`` returned 200 through a globally routable address.

    ``host`` is the TLS name. ``ip`` is the A record the probe connected to,
    not loopback. ``http_status`` is the response code (200 on this signal).
    """
    return Event(
        signal="mcp.edge.probe.succeeded",
        role="observation",
        scope="node",
        payload={
            "host": host,
            "ip": ip,
            "http_status": http_status,
            "path": "/health",
        },
    )


@event_factory
def mcp_edge_probe_failed(
    *,
    host: str,
    ip: str | None,
    error_class: str,
    detail: str,
) -> Event:
    """Public-vantage ``/health`` did not succeed.

    ``error_class`` names the break (``timeout``, ``tls_eof``,
    ``no_public_address``, ``bad_status``, and the connect classes). A DNS
    timeout is ``timeout`` with ``ip`` null. ``detail`` is a short note
    with no credentials.
    """
    return Event(
        signal="mcp.edge.probe.failed",
        role="observation",
        scope="node",
        payload={
            "host": host,
            "ip": ip,
            "error_class": error_class,
            "detail": detail,
            "path": "/health",
        },
    )


@event_factory
def mcp_listener_liveness_silent(*, thread: str, age_ms: int, calls: int) -> Event:
    """A standing listener mailbox has no recent ``mcp.agentbus.wait.called``.

    ``thread`` is the bus thread id. ``age_ms`` is how long since the last
    wait call. ``calls`` is the count inside the lookback that established
    the mailbox as a standing listener.
    """
    return Event(
        signal="mcp.listener.liveness.silent",
        role="observation",
        scope="node",
        payload={"thread": thread, "age_ms": age_ms, "calls": calls},
    )


@event_factory
def mcp_listener_liveness_failed(*, error_class: str, detail: str) -> Event:
    """The listener-silence query itself failed, so silence cannot be judged.

    ``error_class`` is ``query_failed``. ``detail`` is the tool error, trimmed.
    A failed query is not evidence that listeners are healthy.
    """
    return Event(
        signal="mcp.listener.liveness.failed",
        role="observation",
        scope="node",
        payload={"error_class": error_class, "detail": detail},
    )


__all__ = [
    "mcp_edge_probe_failed",
    "mcp_edge_probe_succeeded",
    "mcp_listener_liveness_failed",
    "mcp_listener_liveness_silent",
]
