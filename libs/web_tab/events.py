"""webtab.* observation events (eyes slice)."""

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
def webtab_probe_completed(
    *,
    house_id: str,
    target_id: str,
    tab_count: int,
) -> Event:
    """Read-only probe finished (no navigation side effects)."""
    return Event(
        signal="webtab.probe.completed",
        role="observation",
        scope="node",
        payload={
            "house_id": house_id,
            "target_id": target_id,
            "tab_count": tab_count,
        },
    )


def _ndjson_payload(event: Event) -> bytes:
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "web-tab",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    return (json.dumps(payload) + "\n").encode()


def emit(event: Event) -> None:
    """Best-effort Event Service ingest — never raises into probe callers."""
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


@event_factory
def webtab_write_outcome(
    *,
    op: str,
    ok: bool,
    grant: str | None,
    authorization_source: str | None,
    target_id: str,
    code: str | None = None,
    receipt: str | None = None,
) -> Event:
    return Event(
        signal="webtab.write.outcome",
        role="observation",
        scope="node",
        payload={
            "op": op,
            "ok": ok,
            "grant": grant,
            "authorization_source": authorization_source,
            "target_id": target_id,
            "code": code,
            "receipt": receipt,
        },
    )


def emit_write_outcome(
    *,
    op: str,
    ok: bool,
    grant: str | None,
    authorization_source: str | None,
    target_id: str,
    code: str | None = None,
    receipt: str | None = None,
) -> None:
    emit(
        webtab_write_outcome(
            op=op,
            ok=ok,
            grant=grant,
            authorization_source=authorization_source,
            target_id=target_id,
            code=code,
            receipt=receipt,
        )
    )


def emit_probe_completed(*, house_id: str, target_id: str, tab_count: int) -> None:
    """Best-effort bus emit; never raises into probe callers."""
    emit(
        webtab_probe_completed(
            house_id=house_id,
            target_id=target_id,
            tab_count=tab_count,
        )
    )
