"""AC4: cortex_store publisher prefers EVENTS_INGEST_TCP on satellites."""

from __future__ import annotations

import socket

import pytest

from libs.cortex_store import event_publisher as ep


@pytest.fixture(autouse=True)
def _disable_module_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ep, "_publisher", None)


class _FakeSock:
    instances: list[_FakeSock] = []

    def __init__(self, family: int, *args: object, **kwargs: object) -> None:
        self.family = family
        self.args = (family, *args)
        self.connected: tuple[str, ...] | tuple[str, int] | None = None
        _FakeSock.instances.append(self)

    def settimeout(self, _t: float) -> None:
        pass

    def connect(self, addr: tuple[str, ...] | tuple[str, int]) -> None:
        self.connected = addr

    def close(self) -> None:
        pass


def test_publisher_connect_uses_tcp_when_events_ingest_tcp_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSock.instances.clear()
    monkeypatch.setenv("EVENTS_INGEST_TCP", "10.0.0.67:7101")
    monkeypatch.setattr(ep.socket, "socket", _FakeSock)
    monkeypatch.setattr(ep.threading.Thread, "start", lambda self: None)
    pub = ep._UDSPublisher("/tmp/no-such.sock")
    sock = pub._connect()
    sock.close()
    assert len(_FakeSock.instances) == 1
    assert _FakeSock.instances[0].family == socket.AF_INET
    assert _FakeSock.instances[0].connected == ("10.0.0.67", 7101)


def test_publisher_connect_uses_uds_when_tcp_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeSock.instances.clear()
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.setenv("EVENTS_INGEST_SOCK", "/tmp/test-cortex-events.sock")
    monkeypatch.setattr(ep.socket, "socket", _FakeSock)
    monkeypatch.setattr(ep.threading.Thread, "start", lambda self: None)
    pub = ep._UDSPublisher("/tmp/test-cortex-events.sock")
    pub._connect().close()
    assert _FakeSock.instances[0].family == socket.AF_UNIX
    connected = _FakeSock.instances[0].connected
    assert connected in (
        "/tmp/test-cortex-events.sock",
        ("/tmp/test-cortex-events.sock",),
    )
