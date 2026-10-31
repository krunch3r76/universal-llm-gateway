"""webtab.* observation events (eyes slice)."""

from __future__ import annotations

import contextlib
import json
import os
import socket
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


def emit_probe_completed(*, house_id: str, target_id: str, tab_count: int) -> None:
    """Best-effort bus emit; never raises into probe callers."""
    event = webtab_probe_completed(
        house_id=house_id,
        target_id=target_id,
        tab_count=tab_count,
    )
    _emit_best_effort(event)


def _emit_best_effort(event: Event) -> None:
    sock_path = os.environ.get(
        "EVENT_BUS_SOCK", "/tmp/universal-protocol/event-bus.sock"
    )
    if not sock_path or not os.path.exists(sock_path):
        return
    line = json.dumps(event.to_dict()) + "\n"
    with contextlib.suppress(OSError):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock.connect(sock_path)
            sock.sendall(line.encode("utf-8"))
