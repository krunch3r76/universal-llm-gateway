"""Offline tests for compose_attested Event Service transport (TCP vs UDS).

Arc 6928: Jupiter bundle emit must prefer ``EVENTS_INGEST_TCP`` — UDS-only
silently dropped every row (verify COUNT=0 after ``567a9b49``).
"""

from __future__ import annotations

from typing import Any

import pytest

from claude_bundles import events_compose_attest as eca
from claude_bundles.events_compose_attest import emit_compose_attested

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
def _reset_fake_socks() -> None:
    _FakeSock.instances = []


def _emit_sample() -> None:
    emit_compose_attested(
        ok=True,
        surface="bare_new",
        step="cowork_auto",
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
    monkeypatch.setattr(eca.socket, "socket", _FakeSock)

    _emit_sample()

    assert len(_FakeSock.instances) == 1
    sock = _FakeSock.instances[0]
    assert sock.args[0] == eca.socket.AF_INET
    assert sock.connected == ("10.0.0.67", 7101)
    assert len(sock.sent) == 1
    body = sock.sent[0].decode()
    assert "cdp.generate.compose_attested" in body
    assert "cdp-compose-attest" in body
    assert "0b692df9-bb65-419f-8d08-7c5887eb0837" in body
    assert "8e248e70846b4765af68d57b270b4825" in body
    assert body.endswith("\n")


def test_mirror_uses_host_port_pair_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVENT_SERVICE_INGEST_HOST + port selects TCP without EVENTS_INGEST_TCP."""
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.setenv("EVENT_SERVICE_INGEST_HOST", "192.168.1.10")
    monkeypatch.setenv("EVENTS_INGEST_PORT", "7101")
    monkeypatch.setattr(eca.socket, "socket", _FakeSock)

    _emit_sample()

    sock = _FakeSock.instances[0]
    assert sock.args[0] == eca.socket.AF_INET
    assert sock.connected == ("192.168.1.10", 7101)


def test_mirror_uses_uds_when_tcp_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default path remains AF_UNIX when no TCP ingest target is configured."""
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.delenv("EVENT_SERVICE_INGEST_HOST", raising=False)
    monkeypatch.delenv("EVENTS_INGEST_HOST", raising=False)
    monkeypatch.setenv("EVENTS_INGEST_SOCK", "/tmp/test-compose-attest-events.sock")
    monkeypatch.setattr(eca.socket, "socket", _FakeSock)

    _emit_sample()

    sock = _FakeSock.instances[0]
    assert sock.args[0] == eca.socket.AF_UNIX
    assert sock.connected == ("/tmp/test-compose-attest-events.sock",)
    assert b"compose_attested" in sock.sent[0]


def test_mirror_swallows_socket_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit stays best-effort — connect failures must not raise into compose."""

    class _BoomSock(_FakeSock):
        def connect(self, address: Any) -> None:
            raise ConnectionRefusedError("offline")

    monkeypatch.setenv("EVENTS_INGEST_TCP", "127.0.0.1:7101")
    monkeypatch.setattr(eca.socket, "socket", _BoomSock)
    _emit_sample()
