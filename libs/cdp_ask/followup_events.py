"""Observation events for warm CSE followup paste on the cdp-ask satellite.

Factories build ``cdp_ask.followup.*`` signals; ``emit`` best-effort delivers
NDJSON to hub Event Service (TCP when ``EVENTS_INGEST_TCP`` is set, else UDS).
Callers are the followup paste/reattach path — emit must never raise into them.
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


@event_factory
def cdp_ask_followup_reattach_attempt(
    *,
    chat_url: str,
    holder: str,
    purpose: str | None,
) -> Event:
    """Emit when opt-in warm reattach starts navigating to a CSE URL."""
    return Event(
        signal="cdp_ask.followup.reattach_attempt",
        role="observation",
        scope="node",
        payload={
            "chat_url": chat_url,
            "holder": holder,
            "purpose": purpose,
        },
    )


@event_factory
def cdp_ask_followup_reattach_result(
    *,
    registration_id: str | None,
    lane_created: bool,
    ok: bool,
    error_code: str | None,
) -> Event:
    """Emit after warm reattach completes (lane reused, launched, or typed failure)."""
    return Event(
        signal="cdp_ask.followup.reattach_result",
        role="observation",
        scope="node",
        payload={
            "registration_id": registration_id,
            "lane_created": lane_created,
            "ok": ok,
            "error_code": error_code,
        },
    )


def _ndjson_payload(event: Event) -> bytes:
    """Serialize one ingest line matching Event Service UDS/TCP NDJSON wire."""
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "cdp-ask",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    return (json.dumps(payload) + "\n").encode()


def _parse_tcp_target(raw: str) -> tuple[str, int] | None:
    """Parse ``host:port`` (IPv4/hostname). Returns None when malformed."""
    text = raw.strip()
    if not text or ":" not in text:
        return None
    host, _, port_s = text.rpartition(":")
    host = host.strip()
    if not host or not port_s.strip().isdigit():
        return None
    return host, int(port_s)


def _resolve_tcp_target() -> tuple[str, int] | None:
    """TCP target when set: ``EVENTS_INGEST_TCP`` or host+port env pair."""
    combined = os.environ.get("EVENTS_INGEST_TCP", "").strip()
    if combined:
        return _parse_tcp_target(combined)
    host = (
        os.environ.get("EVENT_SERVICE_INGEST_HOST", "").strip()
        or os.environ.get("EVENTS_INGEST_HOST", "").strip()
    )
    if not host:
        return None
    port_s = (
        os.environ.get("EVENTS_INGEST_PORT", "").strip()
        or os.environ.get("EVENT_INGEST_TCP_PORT", "").strip()
        or "7101"
    )
    if not port_s.isdigit():
        return None
    return host, int(port_s)


def emit(event: Event) -> None:
    """Best-effort Event Service ingest — TCP when configured, else UDS; never raises.

    Prefer ``EVENTS_INGEST_TCP=host:port`` (or ``EVENT_SERVICE_INGEST_HOST`` +
    port env) so Jupiter satellites reach hub ``:7101``; local hub keeps UDS
    via ``EVENTS_INGEST_SOCK`` when TCP is unset.
    """
    line = _ndjson_payload(event)
    with contextlib.suppress(Exception):
        tcp = _resolve_tcp_target()
        if tcp is not None:
            host, port = tcp
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect((host, port))
                sock.sendall(line)
            return
        sock_path = os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(sock_path)
            sock.sendall(line)
