"""Hermetic tests for agent-bus UDS publisher drop counters.

Forces a full queue and a post-dequeue send failure without a live Event Service.
"""

from __future__ import annotations

import json
import logging
import time

import pytest

from agent_bus_store.events import publisher


@pytest.fixture
def reset_drop_counters() -> None:
    publisher.dropped_enqueue = 0
    publisher.dropped_send = 0


def _line(signal: str) -> str:
    return json.dumps({"signal": signal, "payload": {}}) + "\n"


@pytest.mark.offline
def test_full_queue_increments_dropped_enqueue(
    reset_drop_counters: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Drop-oldest on a full queue moves dropped_enqueue, not dropped_send."""
    monkeypatch.setattr(publisher, "_QUEUE_MAX", 2)
    monkeypatch.setattr(publisher._UDSPublisher, "_run", lambda self: None)
    pub = publisher._UDSPublisher("/no/such/agent-bus-events.sock")
    pub.put_nowait(_line("test.enqueue.keep-a"))
    pub.put_nowait(_line("test.enqueue.keep-b"))
    with caplog.at_level(logging.WARNING, logger=publisher.logger.name):
        pub.put_nowait(_line("test.enqueue.overflow"))
    assert publisher.dropped_enqueue == 1
    assert publisher.dropped_send == 0
    assert any(
        "drop-oldest (enqueue)" in rec.message
        and "signal=test.enqueue.keep-a" in rec.message
        for rec in caplog.records
    )


@pytest.mark.offline
def test_send_failure_after_dequeue_increments_dropped_send(
    reset_drop_counters: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """OSError on sendall after get() moves dropped_send, not dropped_enqueue."""

    class _FailingSock:
        def settimeout(self, _timeout: float) -> None:
            return None

        def connect(self, _path: str) -> None:
            return None

        def sendall(self, _data: bytes) -> None:
            raise OSError("injected send failure")

        def close(self) -> None:
            return None

    monkeypatch.setattr(publisher.socket, "socket", lambda *_a, **_k: _FailingSock())
    monkeypatch.setattr(publisher, "_RECONNECT_DELAY", 0.05)
    pub = publisher._UDSPublisher("/ignored.sock")
    with caplog.at_level(logging.WARNING, logger=publisher.logger.name):
        pub.put_nowait(_line("test.send.drop"))
        deadline = time.monotonic() + 2.0
        while publisher.dropped_send == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert publisher.dropped_send == 1
    assert publisher.dropped_enqueue == 0
    assert any(
        "send loss after dequeue" in rec.message
        and "signal=test.send.drop" in rec.message
        for rec in caplog.records
    )


@pytest.mark.offline
def test_signal_from_line_fallbacks() -> None:
    assert publisher._signal_from_line(_line("mcp.agentbus.dispatch.orphaned")) == (
        "mcp.agentbus.dispatch.orphaned"
    )
    assert publisher._signal_from_line("not-json\n") == "<unparseable>"
    assert publisher._signal_from_line("{}\n") == "<unknown>"
