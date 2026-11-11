"""Regression: GIW pytest must not connect to the live event-ingest socket."""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from services.git_integration_worker import events as giw_events
from services.git_integration_worker import git_worker_drain_events as drain_events
from services.git_integration_worker.admission import WorkAdmissionController
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger
from services.git_integration_worker.events import publish_lib_signal
from services.git_integration_worker.tests.events_sink_hermetic import (
    LIVE_EVENTS_INGEST_SOCK,
    live_connect_attempts,
)


@pytest.fixture
def _drain_publisher_registered() -> None:
    drain_events.register_git_worker_drain_event_publisher(publish_lib_signal)


def test_begin_drain_publishes_without_live_ingest_connect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _drain_publisher_registered: None,
) -> None:
    """Drain emit path uses redirected sock; guard never sees live ingest."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GIT_INTEGRATION_SOURCE_REPO", str(repo))
    CursorDispatchLedger._instance = None

    isolated = giw_events._EVENTS_SOCK
    assert isolated != LIVE_EVENTS_INGEST_SOCK
    assert LIVE_EVENTS_INGEST_SOCK not in isolated

    ledger = CursorDispatchLedger.instance()
    controller = WorkAdmissionController(
        ledger=ledger,
        worker_id="test",
        pid=1,
        worker_started_at="2026-01-01T00:00:00+00:00",
    )
    controller.begin_drain(
        reason="hermetic",
        intent_id="intent-hermetic-1",
        drain_epoch=1,
    )
    assert live_connect_attempts == []


def test_publish_lib_signal_delivers_to_tmp_uds_listener(
    tmp_path: Path,
    _drain_publisher_registered: None,
) -> None:
    """Redirected ``_EVENTS_SOCK`` is wired; payload reaches a tmp listener."""
    ingest_path = Path(giw_events._EVENTS_SOCK)
    received: list[bytes] = []
    ready = threading.Event()

    def serve_once() -> None:
        ingest_path.parent.mkdir(parents=True, exist_ok=True)
        if ingest_path.exists():
            ingest_path.unlink()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(ingest_path))
            listener.listen(1)
            ready.set()
            conn, _ = listener.accept()
            with conn:
                received.append(conn.recv(65536))

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    assert ready.wait(timeout=5.0)

    publish_lib_signal(
        "git_worker.drain.started",
        {"intent_id": "uds-hermetic", "worker_id": "w", "pid": 0},
    )
    thread.join(timeout=5.0)
    assert received
    body = json.loads(received[0].decode())
    assert body["signal"] == "git_worker.drain.started"
    assert body["payload"]["intent_id"] == "uds-hermetic"
    assert live_connect_attempts == []


def test_connect_guard_refuses_live_default_path() -> None:
    """Direct AF_UNIX connect to live default is refused and recorded."""
    live_connect_attempts.clear()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        with pytest.raises(ConnectionRefusedError):
            sock.connect(LIVE_EVENTS_INGEST_SOCK)
    assert LIVE_EVENTS_INGEST_SOCK in live_connect_attempts
