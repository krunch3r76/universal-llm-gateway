"""CDP registry observation events — @event_factory + best-effort UDS ingest."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from typing import TYPE_CHECKING

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

if TYPE_CHECKING:
    from claude_bundles.cdp_registry import Registration


@event_factory
def cdp_port_registered(reg: Registration) -> Event:
    return Event(
        signal="cdp.port.registered",
        role="observation",
        scope="node",
        payload=_payload(reg),
    )


@event_factory
def cdp_port_deregistered(reg: Registration) -> Event:
    return Event(
        signal="cdp.port.deregistered",
        role="observation",
        scope="node",
        payload=_payload(reg),
    )


@event_factory
def cdp_port_reattached(reg: Registration) -> Event:
    return Event(
        signal="cdp.port.reattached",
        role="observation",
        scope="node",
        payload=_payload(reg),
    )


def _payload(reg: Registration) -> dict:
    return {
        "registration_id": reg.registration_id,
        "port": reg.port,
        "profile_suffix": reg.profile_suffix,
        "holder": reg.holder,
        "purpose": reg.purpose,
    }


def emit(event: Event) -> None:
    """Best-effort UDS ingest — never raises."""
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    payload = {
        "signal": event.signal,
        "source": "cdp-registry",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    with contextlib.suppress(Exception):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(sock_path)
            sock.sendall((json.dumps(payload) + "\n").encode())
