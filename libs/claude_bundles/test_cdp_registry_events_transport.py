"""Offline tests for CDP registry event transport and occupancy vocabulary."""

from __future__ import annotations

from typing import Any

import pytest

from claude_bundles import cdp_registry_events as events

pytestmark = pytest.mark.offline


class _FakeSock:
    """Minimal socket stand-in recording the selected transport and payload."""

    instances: list[_FakeSock] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.connected: tuple[Any, ...] | None = None
        self.sent: list[bytes] = []
        _FakeSock.instances.append(self)

    def settimeout(self, _value: float) -> None:
        return None

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
    events.emit(
        events.cdp_occupancy_updated(
            live_cse_count=2,
            open_attachment_count=2,
            live_cse_target_count=3,
            live_port_count=4,
            registry_capacity_count=3,
            freshness="fresh",
            previous_freshness="unobserved",
        )
    )


def test_registry_event_prefers_configured_tcp_ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVENTS_INGEST_TCP", "10.0.0.67:7101")
    monkeypatch.delenv("EVENTS_INGEST_SOCK", raising=False)
    monkeypatch.setattr(events.socket, "socket", _FakeSock)

    _emit_sample()

    sock = _FakeSock.instances[0]
    assert sock.args[0] == events.socket.AF_INET
    assert sock.connected == ("10.0.0.67", 7101)
    body = sock.sent[0].decode()
    assert "cdp.occupancy.updated" in body
    assert body.endswith("\n")


def test_registry_event_uses_local_uds_without_tcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.setenv("EVENTS_INGEST_SOCK", "/tmp/test-cdp-events.sock")
    monkeypatch.setattr(events.socket, "socket", _FakeSock)

    _emit_sample()

    sock = _FakeSock.instances[0]
    assert sock.args[0] == events.socket.AF_UNIX
    assert sock.connected == ("/tmp/test-cdp-events.sock",)
