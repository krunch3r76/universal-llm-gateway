"""Observation events for warm CSE followup paste on the cdp-ask satellite."""

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
def cdp_ask_followup_paste_attempt(
    *,
    registration_id: str,
    resolution_path: str,
) -> Event:
    """Emit when a followup paste attempt starts on an attached lane."""
    return Event(
        signal="cdp_ask.followup.paste_attempt",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "resolution_path": resolution_path,
        },
    )


@event_factory
def cdp_ask_followup_paste_verified(
    *,
    registration_id: str,
    resolution_path: str,
    send_verified: bool,
    streaming_at_paste: bool | None,
    error_code: str | None,
) -> Event:
    """Emit after paste verification completes (success or typed failure)."""
    return Event(
        signal="cdp_ask.followup.paste_verified",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "resolution_path": resolution_path,
            "send_verified": send_verified,
            "streaming_at_paste": streaming_at_paste,
            "error_code": error_code,
        },
    )


def emit(event: Event) -> None:
    """Best-effort UDS ingest — never raises."""
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "cdp-ask",
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
