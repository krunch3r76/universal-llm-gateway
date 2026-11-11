"""Hermetic double for event-service UDS ingest during GIW unit tests.

Blocks connects to the live default ingest socket (and the process original
``EVENTS_INGEST_SOCK``) while redirecting ``publish_lib_signal`` to a per-test
tmp path. Mirrors ``cursor_bus_hermetic`` and ``libs/jobs/tests/conftest``.
"""

from __future__ import annotations

import socket
from typing import Any

LIVE_EVENTS_INGEST_SOCK = "/tmp/universal-protocol/events.sock"

# Populated when the connect guard refuses a blocked AF_UNIX target.
live_connect_attempts: list[str] = []


def install_events_sink_hermetic(
    monkeypatch: Any,
    tmp_path: Any,
    *,
    original_events_ingest_sock: str,
) -> str:
    """Redirect event publishes off the live ingest socket for one test."""
    isolated = str(tmp_path / "e.sock")
    live_connect_attempts.clear()

    monkeypatch.setenv("EVENTS_INGEST_SOCK", isolated)

    from services.git_integration_worker import events as giw_events

    monkeypatch.setattr(giw_events, "_EVENTS_SOCK", isolated)

    abs_publisher = _agent_bus_publisher_module()
    if abs_publisher is not None:
        monkeypatch.setattr(abs_publisher, "_EVENTS_SOCK", isolated)

    blocked = {LIVE_EVENTS_INGEST_SOCK, original_events_ingest_sock}

    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Any) -> None:
        if self.family == socket.AF_UNIX and isinstance(address, str):
            if address in blocked:
                live_connect_attempts.append(address)
                raise ConnectionRefusedError(111, "Connection refused", address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    return isolated


def _agent_bus_publisher_module() -> Any | None:
    import sys

    return sys.modules.get("agent_bus_store.events.publisher")
