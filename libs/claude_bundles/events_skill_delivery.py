"""Skill-delivery attest observation events (G1 Option A / invariant 4).

Publishes success **and** fail channel-attest outcomes to Event Service so
pass ≠ silence. Callers: ``attest_delivery_channels`` only — best-effort UDS
ingest must never override fail-closed attest semantics.
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
def cdp_skill_delivery_attested(
    *,
    ok: bool,
    attached: list[str],
    inlined: list[str],
    undelivered: list[str],
    wrong_channel: list[str] | None = None,
    rows: list[dict[str, str]] | None = None,
) -> Event:
    """Observation event for sealed CDP skill channel attest (success or fail)."""
    return Event(
        signal="cdp.skill.delivery_attested",
        role="observation",
        scope="node",
        payload={
            "ok": ok,
            "attached": list(attached),
            "inlined": list(inlined),
            "undelivered": list(undelivered),
            "wrong_channel": list(wrong_channel or []),
            "rows": list(rows or []),
        },
    )


def emit_skill_delivery_attested(
    *,
    ok: bool,
    attached: list[str],
    inlined: list[str],
    undelivered: list[str],
    wrong_channel: list[str] | None = None,
    rows: list[dict[str, str]] | None = None,
) -> Event | None:
    """Build + best-effort-mirror ``cdp.skill.delivery_attested``; never raises.

    Returns the Event when construction succeeds (tests assert payload); returns
    None only if factory construction itself fails (should not happen in prod).
    """
    try:
        event = cdp_skill_delivery_attested(
            ok=ok,
            attached=attached,
            inlined=inlined,
            undelivered=undelivered,
            wrong_channel=wrong_channel,
            rows=rows,
        )
    except Exception:  # noqa: BLE001 — attest path must not fail on telemetry
        return None
    _mirror_to_event_service(event)
    return event


def _mirror_to_event_service(event: Event) -> None:
    """Best-effort UDS ingest — silent when the events sock is down."""
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "cdp-skill-delivery",
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
