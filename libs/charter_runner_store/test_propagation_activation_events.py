"""Tests for activation event factories and UDS publication."""

from __future__ import annotations

import json
import socket
import threading

from charter_runner_store.propagation_activation_events import (
    ManageRestartActivationProgress,
    ManageRestartVerifying,
    publish_activation_event,
)


def test_publish_activation_event_uds_envelope(tmp_path, monkeypatch) -> None:
    import charter_runner_store.propagation_activation_events as mod

    sock_path = tmp_path / "events.sock"
    received: list[bytes] = []
    ready = threading.Event()

    def _listener() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(sock_path))
            server.listen(1)
            ready.set()
            conn, _ = server.accept()
            with conn:
                received.append(conn.recv(4096))
            server.close()

    thread = threading.Thread(target=_listener, daemon=True)
    thread.start()
    assert ready.wait(timeout=2.0)
    monkeypatch.setattr(mod, "_EVENTS_SOCK", str(sock_path))
    event = ManageRestartVerifying(
        intent_id="intent-1",
        validation_id="val-1",
        service="git_integration_worker",
        kill_boundary_at="2026-08-15T00:00:00+00:00",
        boundary_source="kill_return",
    )
    publish_activation_event(event)
    thread.join(timeout=2.0)
    assert len(received) == 1
    payload = json.loads(received[0].decode())
    assert payload["signal"] == "manage.restart.verifying"
    assert payload["role"] == "observation"
    assert payload["scope"] == "node"
    assert payload["payload"]["intent_id"] == "intent-1"


def test_progress_factory_signal() -> None:
    event = ManageRestartActivationProgress(
        intent_id="i",
        validation_id="v",
        progress_class="reachable",
    )
    assert event.signal == "manage.restart.activation.progress"
