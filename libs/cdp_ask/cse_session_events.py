"""Observation events for the public cse_session satellite routes."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def mcp_cse_session_resolved(
    *,
    registration_id: str | None,
    chat_url: str | None,
    state: str,
) -> Event:
    """Emit when identity resolves on a public provenance read."""
    return Event(
        signal="mcp.cse.session.resolved",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "chat_url": chat_url,
            "state": state,
        },
    )


@event_factory
def mcp_cse_session_pasted(
    *,
    registration_id: str | None,
    receipt: str | None,
    send_verified: bool,
    replayed: bool = False,
) -> Event:
    """Emit after paste with receipt — never carries ack_class."""
    return Event(
        signal="mcp.cse.session.pasted",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "receipt": receipt,
            "send_verified": send_verified,
            "replayed": replayed,
        },
    )


@event_factory
def mcp_cse_session_harvested(
    *,
    registration_id: str | None,
    outcome: str,
    ack_class: str,
    turn_count: int = 0,
    reason: str | None = None,
    waited_ms: int | None = None,
) -> Event:
    """Emit when harvest completes or reports an incomplete outcome."""
    return Event(
        signal="mcp.cse.session.harvested",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "outcome": outcome,
            "ack_class": ack_class,
            "turn_count": turn_count,
            "reason": reason,
            "waited_ms": waited_ms,
        },
    )


@event_factory
def mcp_cse_session_acknowledged(
    *,
    registration_id: str | None,
    ack_class: str,
) -> Event:
    """Emit when harvest classifies a typed ACK — not on paste."""
    return Event(
        signal="mcp.cse.session.acknowledged",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "ack_class": ack_class,
        },
    )


@event_factory
def mcp_cse_session_conflict(
    *,
    reason: str,
    registration_id: str | None = None,
    chat_url: str | None = None,
) -> Event:
    """Emit on self-supersession or other conflict refusal."""
    return Event(
        signal="mcp.cse.session.conflict",
        role="observation",
        scope="node",
        payload={
            "reason": reason,
            "registration_id": registration_id,
            "chat_url": chat_url,
        },
    )


def emit(event: Event) -> None:
    """Best-effort NDJSON delivery — must not raise into callers."""
    payload = {
        "signal": event.signal,
        "role": event.role,
        "scope": event.scope,
        "payload": event.payload,
        "ts": time.time(),
    }
    line = json.dumps(payload, separators=(",", ":")) + "\n"
    tcp = os.environ.get("EVENTS_INGEST_TCP", "").strip()
    if tcp:
        host, _, port_str = tcp.partition(":")
        with contextlib.suppress(Exception):
            with socket.create_connection((host, int(port_str)), timeout=2.0) as sock:
                sock.sendall(line.encode())
        return
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    with contextlib.suppress(Exception):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2.0)
            sock.connect(sock_path)
            sock.sendall(line.encode())
