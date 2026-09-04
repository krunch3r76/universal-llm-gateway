"""Advisory events for standing CDP lane health transitions."""

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
def cdp_lane_standing_down(
    *,
    lane: str,
    port: int,
    observed_at: str,
) -> Event:
    """Standing lane is down (unit inactive or CDP refused)."""
    return Event(
        signal="cdp.lane.standing.down",
        role="observation",
        scope="node",
        payload={"lane": lane, "port": port, "observed_at": observed_at},
    )


@event_factory
def cdp_lane_standing_up(
    *,
    lane: str,
    port: int,
    observed_at: str,
) -> Event:
    """Standing lane is up and not lapsed."""
    return Event(
        signal="cdp.lane.standing.up",
        role="observation",
        scope="node",
        payload={"lane": lane, "port": port, "observed_at": observed_at},
    )


@event_factory
def cdp_lane_standing_lapsed(
    *,
    lane: str,
    port: int,
    observed_at: str,
    url_prefix: str,
) -> Event:
    """Standing lane CDP answers but vendor session is lapsed."""
    return Event(
        signal="cdp.lane.standing.lapsed",
        role="observation",
        scope="node",
        payload={
            "lane": lane,
            "port": port,
            "observed_at": observed_at,
            "url_prefix": url_prefix,
        },
    )


def _emit(event: Event) -> None:
    sock_path = os.environ.get(
        "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
    )
    ingest_tcp = os.environ.get("EVENTS_INGEST_TCP", "").strip()
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "cdp-ask",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    body = (json.dumps(payload) + "\n").encode()
    if ingest_tcp:
        host, _, port_s = ingest_tcp.rpartition(":")
        if host and port_s.isdigit():
            with contextlib.suppress(Exception):
                with socket.create_connection((host, int(port_s)), timeout=1.0) as s:
                    s.sendall(body)
            return
    with contextlib.suppress(Exception):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(sock_path)
            s.sendall(body)


def emit_standing_down(*, lane: str, port: int, observed_at: str) -> None:
    _emit(cdp_lane_standing_down(lane=lane, port=port, observed_at=observed_at))


def emit_standing_up(*, lane: str, port: int, observed_at: str) -> None:
    _emit(cdp_lane_standing_up(lane=lane, port=port, observed_at=observed_at))


def emit_standing_lapsed(
    *, lane: str, port: int, observed_at: str, url_prefix: str
) -> None:
    _emit(
        cdp_lane_standing_lapsed(
            lane=lane,
            port=port,
            observed_at=observed_at,
            url_prefix=url_prefix,
        )
    )
