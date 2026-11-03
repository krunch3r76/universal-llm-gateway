"""Offline tests for skill-delivery Event Service transport (TCP vs UDS).

Remote ``cdp_ask`` sets ``EVENTS_INGEST_TCP``; UDS-only mirror dropped rows on
Jupiter (arc 12286 / friction 41800).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import pytest

from claude_bundles import events_skill_delivery as esd
from claude_bundles.events_skill_delivery import (
    cdp_skill_delivery_attested,
    emit_skill_delivery_attested,
    test_flush_mirror_worker,
    test_reset_mirror_transport_state,
)

pytestmark = pytest.mark.offline


class _FakeSock:
    """Minimal socket stand-in recording connect/sendall for transport asserts."""

    instances: list[_FakeSock] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.connected: tuple[Any, ...] | None = None
        self.sent: list[bytes] = []
        self.timeout: float | None = None
        _FakeSock.instances.append(self)

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def connect(self, address: Any) -> None:
        self.connected = address if isinstance(address, tuple) else (address,)

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def __enter__(self) -> _FakeSock:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_mirror_state() -> None:
    _FakeSock.instances = []
    test_reset_mirror_transport_state()
    yield
    test_reset_mirror_transport_state()


def _emit_sample() -> None:
    emit_skill_delivery_attested(
        ok=True,
        attached=["reasoning-posture"],
        inlined=[],
        undelivered=[],
        execution_id="0b692df9-bb65-419f-8d08-7c5887eb0837",
        satellite_execution_id="8e248e70846b4765af68d57b270b4825",
    )


def test_mirror_uses_tcp_when_events_ingest_tcp_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EVENTS_INGEST_TCP forces AF_INET NDJSON; UDS must not be used."""
    monkeypatch.setenv("EVENTS_INGEST_TCP", "10.0.0.67:7101")
    monkeypatch.delenv("EVENTS_INGEST_SOCK", raising=False)
    monkeypatch.delenv("EVENT_SERVICE_INGEST_HOST", raising=False)
    monkeypatch.delenv("EVENTS_INGEST_HOST", raising=False)
    monkeypatch.setattr(esd.socket, "socket", _FakeSock)

    _emit_sample()
    test_flush_mirror_worker()

    assert len(_FakeSock.instances) == 1
    sock = _FakeSock.instances[0]
    assert sock.args[0] == esd.socket.AF_INET
    assert sock.connected == ("10.0.0.67", 7101)
    assert len(sock.sent) == 1
    body = sock.sent[0].decode()
    assert "cdp.skill.delivery_attested" in body
    assert "cdp-skill-delivery" in body
    assert body.endswith("\n")


def test_mirror_uses_uds_when_tcp_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default path remains AF_UNIX when no TCP ingest target is configured."""
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.delenv("EVENT_SERVICE_INGEST_HOST", raising=False)
    monkeypatch.delenv("EVENTS_INGEST_HOST", raising=False)
    monkeypatch.setenv("EVENTS_INGEST_SOCK", "/tmp/test-skill-delivery-events.sock")
    monkeypatch.setattr(esd.socket, "socket", _FakeSock)

    _emit_sample()
    test_flush_mirror_worker()

    sock = _FakeSock.instances[0]
    assert sock.args[0] == esd.socket.AF_UNIX
    assert sock.connected == ("/tmp/test-skill-delivery-events.sock",)
    assert b"delivery_attested" in sock.sent[0]


def test_mirror_enqueue_returns_before_slow_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror must not block the caller on socket I/O (a:41825)."""

    def _slow_send(_event: object) -> None:
        time.sleep(1.0)

    monkeypatch.setattr(esd, "_mirror_send_sync", _slow_send)
    started = time.monotonic()
    esd._mirror_to_event_service(
        cdp_skill_delivery_attested(
            ok=True, attached=[], inlined=[], undelivered=[]
        )
    )
    elapsed = time.monotonic() - started
    assert elapsed < 0.05
    test_flush_mirror_worker(timeout=3.0)


def test_circuit_breaker_skips_sends_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After one failed send, enqueues inside 30s are dropped (a:41825)."""
    calls: list[int] = []

    def _fail_send(_event: object) -> None:
        calls.append(1)
        raise ConnectionRefusedError("offline")

    monkeypatch.setattr(esd, "_mirror_send_sync", _fail_send)

    event = cdp_skill_delivery_attested(
        ok=True, attached=[], inlined=[], undelivered=[]
    )
    esd._mirror_to_event_service(event)
    test_flush_mirror_worker()
    assert len(calls) == 1

    esd._mirror_to_event_service(event)
    test_flush_mirror_worker()
    assert len(calls) == 1

    with esd._mirror_state_lock:
        esd._circuit_open_until = 0.0
    esd._mirror_to_event_service(event)
    test_flush_mirror_worker()
    assert len(calls) == 2


def test_mirror_warning_throttle_per_transport_target(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """At most one warning per 300s per (transport, target); counts roll forward."""
    caplog.set_level(logging.WARNING)

    def _fail_send(_event: object) -> None:
        raise ConnectionRefusedError("offline")

    monkeypatch.setattr(esd, "_mirror_send_sync", _fail_send)
    monkeypatch.setenv("EVENTS_INGEST_TCP", "127.0.0.1:7101")
    t0 = 2000.0
    seq = [
        t0,
        t0,
        t0 + 1.0,
        t0 + 1.0,
        t0 + 301.0,
        t0 + 301.0,
        t0 + 301.0,
        t0 + 301.0,
    ]
    monkeypatch.setattr(esd.time, "monotonic", lambda: seq.pop(0) if seq else t0 + 400.0)

    event = cdp_skill_delivery_attested(
        ok=True, attached=[], inlined=[], undelivered=[]
    )
    for _ in range(2):
        esd._mirror_to_event_service(event)
        test_flush_mirror_worker()

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings) == 1
    assert "transport=tcp" in warnings[0].message
    assert "target=127.0.0.1:7101" in warnings[0].message

    esd._mirror_to_event_service(event)
    test_flush_mirror_worker()
    warnings2 = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings2) == 2
    assert "failures=" in warnings2[1].message
    assert "drops=" in warnings2[1].message


def test_full_mirror_queue_drops_and_counts() -> None:
    """When the bounded queue is full, new events are dropped and counted."""
    event = cdp_skill_delivery_attested(
        ok=True, attached=[], inlined=[], undelivered=[]
    )
    for _ in range(esd._MIRROR_QUEUE_MAXSIZE):
        esd._mirror_queue.put_nowait(event)
    esd._mirror_to_event_service(event)
    assert esd._queue_drop_count >= 1


def test_emit_survives_thread_start_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror worker start failure must not escape emit_skill_delivery_attested."""

    def _boom_start(_self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(esd.threading.Thread, "start", _boom_start)
    event = emit_skill_delivery_attested(
        ok=True,
        attached=["x"],
        inlined=[],
        undelivered=[],
    )
    assert event is not None
    assert event.signal == "cdp.skill.delivery_attested"


def test_mirror_survives_invalid_events_ingest_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unicode digit port env must not raise through emit (a:41830 G1)."""
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.setenv("EVENT_SERVICE_INGEST_HOST", "127.0.0.1")
    monkeypatch.setenv("EVENTS_INGEST_PORT", "\u00b2")
    event = emit_skill_delivery_attested(
        ok=True,
        attached=[],
        inlined=[],
        undelivered=[],
    )
    assert event is not None


def test_mirror_logs_once_and_swallows_socket_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Connect failures log at warning with transport/target; emit never raises."""

    class _BoomSock(_FakeSock):
        def connect(self, address: Any) -> None:
            raise ConnectionRefusedError("offline")

    monkeypatch.setenv("EVENTS_INGEST_TCP", "127.0.0.1:7101")
    monkeypatch.setattr(esd.socket, "socket", _BoomSock)
    caplog.set_level(logging.WARNING)

    _emit_sample()
    _emit_sample()
    test_flush_mirror_worker()

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings) == 1
    assert "transport=tcp" in warnings[0].message
    assert "target=127.0.0.1:7101" in warnings[0].message
