"""Offline tests for followup_events emit transport (UDS vs TCP) — no live network."""

from __future__ import annotations

from typing import Any

import pytest
from universal_event_bus.events.event import Event

from cdp_ask import followup_events
from cdp_ask.followup_events import (
    cdp_ask_attended_refused,
    cdp_ask_attended_resolve,
    cdp_ask_followup_reattach_attempt,
    cdp_ask_followup_unbound_capped,
    emit,
)

pytestmark = pytest.mark.offline


def _sample_event() -> Event:
    """Build a reattach_attempt Event via the sanctioned factory."""
    return cdp_ask_followup_reattach_attempt(
        chat_url="https://claude.ai/cowork/cse_x",
        holder="h1",
        purpose="operator-proxy",
    )


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


def test_emit_uses_tcp_when_events_ingest_tcp_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EVENTS_INGEST_TCP forces AF_INET NDJSON; UDS must not be used."""
    monkeypatch.setenv("EVENTS_INGEST_TCP", "10.0.0.67:7101")
    monkeypatch.delenv("EVENTS_INGEST_SOCK", raising=False)
    monkeypatch.delenv("EVENT_SERVICE_INGEST_HOST", raising=False)
    monkeypatch.delenv("EVENTS_INGEST_HOST", raising=False)
    monkeypatch.setattr(followup_events.socket, "socket", _FakeSock)

    emit(_sample_event())

    assert len(_FakeSock.instances) == 1
    sock = _FakeSock.instances[0]
    assert sock.args[0] == followup_events.socket.AF_INET
    assert sock.connected == ("10.0.0.67", 7101)
    assert len(sock.sent) == 1
    body = sock.sent[0].decode()
    assert "cdp_ask.followup.reattach_attempt" in body
    assert body.endswith("\n")


def test_emit_uses_host_port_pair_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """EVENT_SERVICE_INGEST_HOST + port selects TCP without EVENTS_INGEST_TCP."""
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.setenv("EVENT_SERVICE_INGEST_HOST", "192.168.1.10")
    monkeypatch.setenv("EVENTS_INGEST_PORT", "7101")
    monkeypatch.setattr(followup_events.socket, "socket", _FakeSock)

    emit(_sample_event())

    sock = _FakeSock.instances[0]
    assert sock.args[0] == followup_events.socket.AF_INET
    assert sock.connected == ("192.168.1.10", 7101)


def test_emit_uses_uds_when_tcp_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default path remains AF_UNIX when no TCP ingest target is configured."""
    monkeypatch.delenv("EVENTS_INGEST_TCP", raising=False)
    monkeypatch.delenv("EVENT_SERVICE_INGEST_HOST", raising=False)
    monkeypatch.delenv("EVENTS_INGEST_HOST", raising=False)
    monkeypatch.setenv("EVENTS_INGEST_SOCK", "/tmp/test-events.sock")
    monkeypatch.setattr(followup_events.socket, "socket", _FakeSock)

    emit(_sample_event())

    sock = _FakeSock.instances[0]
    assert sock.args[0] == followup_events.socket.AF_UNIX
    assert sock.connected == ("/tmp/test-events.sock",)
    assert b"reattach_attempt" in sock.sent[0]


def test_emit_swallows_socket_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emit stays best-effort — connect failures must not raise into followup."""

    class _BoomSock(_FakeSock):
        def connect(self, address: Any) -> None:
            raise ConnectionRefusedError("offline")

    monkeypatch.setenv("EVENTS_INGEST_TCP", "127.0.0.1:7101")
    monkeypatch.setattr(followup_events.socket, "socket", _BoomSock)
    emit(_sample_event())


def test_factory_still_builds_reattach_attempt() -> None:
    """Smoke: factory returns the observation Event used by emit callers."""
    event = cdp_ask_followup_reattach_attempt(
        chat_url="https://claude.ai/cowork/cse_smoke",
        holder="h",
        purpose=None,
    )
    assert event.signal == "cdp_ask.followup.reattach_attempt"


def test_attended_and_unbound_factories() -> None:
    resolved = cdp_ask_attended_resolve(
        registration_id="r1",
        cdp_url="http://127.0.0.1:9223",
        chat_url="https://claude.ai/cowork/cse_x",
        purpose="operator-proxy",
        source="cse-session-registry",
    )
    assert resolved.signal == "cdp_ask.attended.resolve"
    refused = cdp_ask_attended_refused(code="no_attended_cse", candidates_considered=0)
    assert refused.payload["code"] == "no_attended_cse"
    capped = cdp_ask_followup_unbound_capped(
        registration_id="r1", receipt="dom_paste", target_binding="unbound"
    )
    assert capped.signal == "cdp_ask.followup.unbound_capped"
