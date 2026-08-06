"""Observation events for STOP-ACK stream-stop check-in backup liveness."""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from typing import Any, Literal

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory

AckRoute = Literal["intentional", "unintentional", "parked"]
CheckinRoute = Literal["paste", "bus_wake+pager"]


@event_factory
def cdp_ask_stop_ack_checkin_attempt(
    *,
    execution_id: str,
    purpose: str | None,
    route: CheckinRoute,
    lane_created: bool,
    registration_id: str | None = None,
) -> Event:
    """Emit when a STOP-ACK check-in paste is attempted or pager fallback is chosen."""
    return Event(
        signal="cdp_ask.stop_ack.checkin_attempt",
        role="observation",
        scope="node",
        payload={
            "execution_id": execution_id,
            "registration_id": registration_id,
            "purpose": purpose,
            "route": route,
            "lane_created": lane_created,
        },
    )


@event_factory
def cdp_ask_stop_ack_ack(
    *,
    execution_id: str,
    ack: AckRoute,
    job: str | None = None,
) -> Event:
    """Emit when a scraped STOP-ACK token is parsed from the model reply."""
    payload: dict[str, str | None] = {
        "execution_id": execution_id,
        "ack": ack,
    }
    if job is not None:
        payload["job"] = job
    return Event(
        signal="cdp_ask.stop_ack.ack",
        role="observation",
        scope="node",
        payload=payload,
    )


@event_factory
def cdp_ask_stop_ack_no_ack(
    *,
    execution_id: str,
    registration_id: str | None = None,
) -> Event:
    """Emit when STOP-ACK TTL expires without a parsed ACK (ghost-reap candidate)."""
    return Event(
        signal="cdp_ask.stop_ack.no_ack",
        role="observation",
        scope="node",
        payload={
            "execution_id": execution_id,
            "registration_id": registration_id,
            "ghost_reap_candidate": True,
        },
    )


def _ndjson_payload(event: Event) -> bytes:
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "cdp-ask",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    return (json.dumps(payload) + "\n").encode()


def emit(event: Event) -> None:
    """Best-effort Event Service ingest; never raises."""
    line = _ndjson_payload(event)
    with contextlib.suppress(Exception):
        combined = os.environ.get("EVENTS_INGEST_TCP", "").strip()
        if combined and ":" in combined:
            host, _, port_s = combined.rpartition(":")
            if host and port_s.isdigit():
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(1.0)
                    sock.connect((host.strip(), int(port_s)))
                    sock.sendall(line)
                return
        sock_path = os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(sock_path)
            sock.sendall(line)
