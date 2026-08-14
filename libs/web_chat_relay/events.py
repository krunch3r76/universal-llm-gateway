"""Low-frequency observation events for the grok↔Cowork relay.

Poll ticks stay silent. Emit is best-effort and never raises into the loop.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from typing import Any

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def web_chat_relay_started(
    *,
    grok_url: str,
    claude_chat_url: str | None,
) -> Event:
    """Relay attached both ends and entered the poll loop."""
    return Event(
        signal="web_chat.relay.started",
        role="observation",
        scope="node",
        payload={"grok_url": grok_url, "claude_chat_url": claude_chat_url},
    )


@event_factory
def web_chat_relay_turn_relayed(
    *,
    direction: str,
    body_sha256: str,
    relay_index: int,
) -> Event:
    """One completed assistant turn was pasted onto the other product."""
    return Event(
        signal="web_chat.relay.turn_relayed",
        role="observation",
        scope="node",
        payload={
            "direction": direction,
            "body_sha256": body_sha256,
            "relay_index": relay_index,
        },
    )


@event_factory
def web_chat_relay_auth_missing(*, grok_url: str, page_url: str) -> Event:
    """Attended :9222 tab is not signed into the target grok.com chat."""
    return Event(
        signal="web_chat.relay.auth_missing",
        role="observation",
        scope="node",
        payload={"grok_url": grok_url, "page_url": page_url},
    )


@event_factory
def web_chat_relay_stopped(*, reason: str, relays: int) -> Event:
    """Relay loop exited (SIGINT, max-relays, stop file, or error)."""
    return Event(
        signal="web_chat.relay.stopped",
        role="observation",
        scope="node",
        payload={"reason": reason, "relays": relays},
    )


def _ndjson_payload(event: Event) -> bytes:
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "web-chat-relay",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    return (json.dumps(payload) + "\n").encode()


def emit(event: Event) -> None:
    """Best-effort Event Service ingest — never raises."""
    line = _ndjson_payload(event)
    with contextlib.suppress(Exception):
        sock_path = os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        )
        tcp = os.environ.get("EVENTS_INGEST_TCP", "").strip()
        if tcp and ":" in tcp:
            host, _, port_s = tcp.rpartition(":")
            if host and port_s.isdigit():
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(1.0)
                    sock.connect((host, int(port_s)))
                    sock.sendall(line)
                return
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(sock_path)
            sock.sendall(line)
