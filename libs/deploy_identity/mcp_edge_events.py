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


@event_factory
def mcp_edge_grokbot_tunnel_stale(
    *,
    disconnected_s: float,
    desktop_pid: int | None,
) -> Event:
    """Grok Bot desktop is up but the sand user-computer watch is not connected."""
    return Event(
        signal="mcp.edge.grokbot_tunnel.stale",
        role="observation",
        scope="node",
        payload={
            "disconnected_s": round(disconnected_s, 1),
            "desktop_pid": desktop_pid,
        },
    )


@event_factory
def mcp_edge_grokbot_loader_stuck(
    *,
    since_tools_list_s: float,
    gc1_client_ip: str,
) -> Event:
    """Box MCP completed handshake without a following tools/call while edge is healthy."""
    return Event(
        signal="mcp.edge.grokbot_loader.stuck",
        role="observation",
        scope="node",
        payload={
            "since_tools_list_s": round(since_tools_list_s, 1),
            "gc1_client_ip": gc1_client_ip,
        },
    )


@event_factory
def mcp_edge_grokbot_probe_failed(*, error_class: str, detail: str) -> Event:
    """Grok Bot egress watch inputs could not be read; no recovery action is taken."""
    return Event(
        signal="mcp.edge.grokbot.probe.failed",
        role="observation",
        scope="node",
        payload={"error_class": error_class, "detail": detail},
    )


@event_factory
def mcp_edge_grokbot_watch_ok(
    *,
    watch_connected: bool,
    health_http_status: int | None,
    hm1_tools_call_ok: bool,
    undici_idle_s: float | None,
    health_checked: bool,
) -> Event:
    """Grok Bot egress watch completed a healthy tick.

    ``health_checked`` is false when the probe was skipped (no hostname or
    no public IPv4); that tick still emits watch.ok. A probe that failed
    because resolution or ``fetch_health`` raised (refused, TLS reset,
    timeout) is a failure tick: no ok, and the rate window is cleared.
    The loop emits one ok per 120 s under steady health, and at worst one
    per 60 s when ticks alternate healthy and failing.
    """
    payload: dict[str, object] = {
        "watch_connected": watch_connected,
        "health_http_status": health_http_status,
        "hm1_tools_call_ok": hm1_tools_call_ok,
        "undici_idle_s": (
            round(undici_idle_s, 1) if undici_idle_s is not None else None
        ),
        "health_checked": health_checked,
    }
    return Event(
        signal="mcp.edge.grokbot.watch.ok",
        role="observation",
        scope="node",
        payload=payload,
    )


@event_factory
def mcp_edge_grokbot_context_hop_due(*, names: list[str], thread_id: str) -> Event:
    """A context-hop notice is owed to grok-bot-gc1."""
    return Event(
        signal="mcp.edge.grokbot.context_hop.due",
        role="observation",
        scope="node",
        payload={"names": names, "thread_id": thread_id},
    )


@event_factory
def mcp_edge_grokbot_context_hop_notify_failed(
    *, error_class: str, detail: str
) -> Event:
    """The context-hop bus reply failed. Cooldown stays unset."""
    return Event(
        signal="mcp.edge.grokbot.context_hop.notify_failed",
        role="observation",
        scope="node",
        payload={"error_class": error_class, "detail": detail},
    )


__all__ = [
    "mcp_edge_grokbot_context_hop_due",
    "mcp_edge_grokbot_context_hop_notify_failed",
    "mcp_edge_grokbot_loader_stuck",
    "mcp_edge_grokbot_probe_failed",
    "mcp_edge_grokbot_tunnel_stale",
    "mcp_edge_grokbot_watch_ok",
    "mcp_edge_probe_failed",
    "mcp_edge_probe_succeeded",
    "mcp_listener_liveness_failed",
    "mcp_listener_liveness_silent",
]
