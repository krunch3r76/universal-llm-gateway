"""Offline tests for skill-delivery Event Service transport (TCP vs UDS).

Remote ``cdp_ask`` sets ``EVENTS_INGEST_TCP``; UDS-only mirror dropped rows on
Jupiter (arc 12286 / friction 41800).
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from claude_bundles import events_skill_delivery as esd
from claude_bundles.events_skill_delivery import emit_skill_delivery_attested

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
def _reset_fake_socks_and_mirror_log(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeSock.instances = []
    monkeypatch.setattr(esd, "_mirror_failure_logged", False)


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

    sock = _FakeSock.instances[0]
    assert sock.args[0] == esd.socket.AF_UNIX
    assert sock.connected == ("/tmp/test-skill-delivery-events.sock",)
    assert b"delivery_attested" in sock.sent[0]


def test_mirror_logs_once_and_swallows_socket_errors(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Connect failures log once at warning with transport/target; emit never raises."""

    class _BoomSock(_FakeSock):
        def connect(self, address: Any) -> None:
            raise ConnectionRefusedError("offline")

    monkeypatch.setenv("EVENTS_INGEST_TCP", "127.0.0.1:7101")
    monkeypatch.setattr(esd.socket, "socket", _BoomSock)
    caplog.set_level(logging.WARNING)

    _emit_sample()
    _emit_sample()

    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings) == 1
    assert "transport=tcp" in warnings[0].message
    assert "target=127.0.0.1:7101" in warnings[0].message
