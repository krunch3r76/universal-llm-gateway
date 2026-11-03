"""Offline tests for skill-delivery Event Service transport (TCP vs UDS).

Remote ``cdp_ask`` sets ``EVENTS_INGEST_TCP``; UDS-only mirror dropped rows on
Jupiter (arc 12286 / friction 41800).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import time
from typing import Any

import pytest

from claude_bundles import events_skill_delivery as esd
from claude_bundles.events_skill_delivery import (
    _MirrorFlush,
    cdp_skill_delivery_attested,
    drain_mirror_queue_at_exit,
    emit_skill_delivery_attested,
    flush_mirror_worker_for_tests,
    reset_mirror_state_for_tests,
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
    reset_mirror_state_for_tests()
    yield
    flush_mirror_worker_for_tests(timeout=2.0)
    reset_mirror_state_for_tests()


def _seed_warning_throttle(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    """S4d: suppress steady-state throttle so _warning_counts survive to assert."""
    monkeypatch.setattr(esd, "_now", lambda: 1000.0)
    try:
        key = esd._mirror_transport_target()
    except Exception:
        key = ("uds", "unknown")
    monkeypatch.setitem(esd._last_warning_at, key, esd._now())
    return key


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
    flush_mirror_worker_for_tests()

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
    flush_mirror_worker_for_tests()

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
    flush_mirror_worker_for_tests(timeout=3.0)


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
    flush_mirror_worker_for_tests()
    assert len(calls) == 1

    esd._mirror_to_event_service(event)
    flush_mirror_worker_for_tests()
    assert len(calls) == 1

    with esd._mirror_state_lock:
        esd._circuit_open_until = 0.0
    esd._mirror_to_event_service(event)
    flush_mirror_worker_for_tests()
    assert len(calls) == 2


def test_mirror_warning_throttle_per_transport_target(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """At most one warning per 300s per (transport, target); counts roll forward."""
    caplog.set_level(logging.WARNING, logger="claude_bundles.events_skill_delivery")
    t0 = 2000.0
    monkeypatch.setattr(esd, "_now", lambda: t0)
    esd._record_send_failure("tcp", "127.0.0.1:7101", ConnectionRefusedError("offline"))
    esd._record_send_failure("tcp", "127.0.0.1:7101", ConnectionRefusedError("offline"))
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings) == 1
    assert "transport=tcp" in warnings[0].message
    assert "target=127.0.0.1:7101" in warnings[0].message

    monkeypatch.setattr(esd, "_now", lambda: t0 + 301.0)
    esd._record_send_failure("tcp", "127.0.0.1:7101", ConnectionRefusedError("offline"))
    warnings2 = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings2) == 2
    assert "failures=" in warnings2[1].message
    assert "drops=" in warnings2[1].message


def test_full_mirror_queue_drops_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bounded queue is full, new events are dropped and counted."""
    hold = threading.Event()

    def _hold_send(_event: object) -> None:
        hold.wait(timeout=2.0)

    monkeypatch.setattr(esd, "_mirror_send_sync", _hold_send)
    event = cdp_skill_delivery_attested(
        ok=True, attached=[], inlined=[], undelivered=[]
    )
    esd._mirror_to_event_service(event)
    filler = b'{"signal":"x"}\n'
    for _ in range(esd._MIRROR_QUEUE_MAXSIZE):
        try:
            esd._mirror_queue.put_nowait(filler)
        except esd.queue.Full:
            break
    esd._mirror_to_event_service(event)
    assert esd._queue_drop_count >= 1
    hold.set()
    flush_mirror_worker_for_tests(timeout=3.0)


def test_worker_breaker_drops_queued_events_after_one_failed_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed send opens breaker; remaining queued items drop without send."""
    attempts: list[int] = []

    def _fail_send(_event: object) -> None:
        attempts.append(1)
        raise ConnectionRefusedError("blackhole")

    monkeypatch.setattr(esd, "_mirror_send_sync", _fail_send)
    event = cdp_skill_delivery_attested(
        ok=True, attached=[], inlined=[], undelivered=[]
    )
    for _ in range(10):
        esd._mirror_to_event_service(event)
    flush_mirror_worker_for_tests(timeout=3.0)
    assert len(attempts) == 1
    assert esd._circuit_skip_count >= 9


def test_queue_drop_emits_throttled_warning_without_send_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Queue-full drops log through the same throttle as send failures."""
    caplog.set_level(logging.WARNING)
    hold = threading.Event()
    started = threading.Event()

    def _hold_send(_event: object) -> None:
        started.set()
        hold.wait(timeout=2.0)

    monkeypatch.setattr(esd, "_mirror_send_sync", _hold_send)
    monkeypatch.setattr(esd, "_now", lambda: 5000.0)
    event = cdp_skill_delivery_attested(
        ok=True, attached=[], inlined=[], undelivered=[]
    )
    esd._mirror_to_event_service(event)
    assert started.wait(timeout=1.0)
    filler = b'{"signal":"x"}\n'
    for _ in range(esd._MIRROR_QUEUE_MAXSIZE):
        try:
            esd._mirror_queue.put_nowait(filler)
        except esd.queue.Full:
            break
    esd._mirror_to_event_service(event)
    hold.set()
    flush_mirror_worker_for_tests(timeout=3.0)
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings) == 1
    assert "drops=" in warnings[0].message
    assert "failures=0" in warnings[0].message


def test_fork_pid_change_starts_fresh_mirror_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After fork, a new worker thread must start instead of inheriting a stale flag."""
    monkeypatch.setattr(esd, "_mirror_send_sync", lambda _line: None)
    starts: list[int] = []
    real_start = esd._start_mirror_thread
    pid = os.getpid()

    def _track_start(thread: esd.threading.Thread) -> None:
        starts.append(1)
        return real_start(thread)

    monkeypatch.setattr(esd, "_start_mirror_thread", _track_start)
    monkeypatch.setattr(esd, "_getpid", lambda: pid)
    emit_skill_delivery_attested(ok=True, attached=[], inlined=[], undelivered=[])
    flush_mirror_worker_for_tests(timeout=2.0)
    assert len(starts) == 1

    monkeypatch.setattr(esd, "_getpid", lambda: pid + 1)
    emit_skill_delivery_attested(ok=True, attached=[], inlined=[], undelivered=[])
    flush_mirror_worker_for_tests(timeout=2.0)
    assert len(starts) == 2


def test_emit_survives_thread_start_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror worker start failure must not escape emit_skill_delivery_attested."""

    def _boom_start(_self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(esd, "_start_mirror_thread", lambda _t: _boom_start(_t))
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
    monkeypatch.setattr(esd.socket, "socket", _FakeSock)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connect failures log at warning with transport/target; emit never raises."""
    reset_mirror_state_for_tests()
    warnings_seen: list[tuple[str, str]] = []
    real_emit_warning = esd._emit_mirror_warning_if_due

    def _track_warning(
        key: tuple[str, str],
        transport: str,
        target: str,
        exc: Exception,
    ) -> None:
        now = esd._now()
        with esd._mirror_state_lock:
            last = esd._last_warning_at.get(key, 0.0)
            throttled = now - last < esd._WARNING_INTERVAL_SECONDS
        if not throttled:
            warnings_seen.append((transport, target))
        return real_emit_warning(key, transport, target, exc)

    class _BoomSock(_FakeSock):
        def connect(self, address: Any) -> None:
            raise ConnectionRefusedError("offline")

    monkeypatch.setattr(esd, "_emit_mirror_warning_if_due", _track_warning)
    monkeypatch.setenv("EVENTS_INGEST_TCP", "127.0.0.1:7101")
    monkeypatch.setattr(esd.socket, "socket", _BoomSock)
    t0 = 7000.0
    monkeypatch.setattr(esd, "_now", lambda: t0)

    _emit_sample()
    _emit_sample()
    flush_mirror_worker_for_tests(timeout=5.0)

    assert len(warnings_seen) == 1
    assert warnings_seen[0] == ("tcp", "127.0.0.1:7101")
    assert esd._now() < esd._circuit_open_until
    assert esd._circuit_skip_count >= 1


class _NotJsonSerializable:
    pass


def test_non_serializable_row_drops_without_opening_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serialization failure is a drop only; a following valid event still sends (N4)."""
    sent: list[bytes] = []

    def _capture_send(line: bytes) -> None:
        sent.append(line)

    monkeypatch.setattr(esd, "_mirror_send_sync", _capture_send)

    bad = cdp_skill_delivery_attested(
        ok=True, attached=[], inlined=[], undelivered=[], rows=[{"x": "1"}]
    )
    bad.payload["rows"] = [{"bad": _NotJsonSerializable()}]
    esd._mirror_to_event_service(bad)
    good = cdp_skill_delivery_attested(
        ok=True, attached=["ok"], inlined=[], undelivered=[]
    )
    esd._mirror_to_event_service(good)
    flush_mirror_worker_for_tests(timeout=3.0)
    assert len(sent) == 1
    assert b"ok" in sent[0]
    assert esd._now() >= esd._circuit_open_until


@pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork unavailable")
def test_fork_child_resets_mirror_queue_and_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fork child reinitializes queue and locks (N2); real os.fork."""
    monkeypatch.setattr(esd, "_mirror_send_sync", lambda _line: None)
    esd._mirror_to_event_service(
        cdp_skill_delivery_attested(
            ok=True, attached=[], inlined=[], undelivered=[]
        )
    )
    assert esd._mirror_state_lock.acquire(blocking=False)
    child = os.fork()
    if child == 0:
        try:
            assert esd._mirror_queue.empty()
            child_lock = esd._mirror_state_lock
            assert child_lock.acquire(timeout=1.0)
            child_lock.release()
            os._exit(0)
        except Exception:
            os._exit(1)
    assert child > 0
    esd._mirror_state_lock.release()
    _, status = os.waitpid(child, 0)
    assert os.WEXITSTATUS(status) == 0


def test_mirror_internal_error_emits_throttled_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Programming errors in the mirror path increment internal_errors (N3)."""
    reset_mirror_state_for_tests()
    caplog.clear()
    caplog.set_level(logging.WARNING, logger="claude_bundles.events_skill_delivery")
    monkeypatch.setattr(esd, "_now", lambda: 9000.0)

    def _broken_target() -> tuple[str, str]:
        raise NameError("mirror wiring bug")

    monkeypatch.setattr(esd, "_mirror_transport_target", _broken_target)
    esd._mirror_to_event_service(
        cdp_skill_delivery_attested(
            ok=True, attached=[], inlined=[], undelivered=[]
        )
    )
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings) == 1
    assert "internal_errors=1" in warnings[0].message
    assert esd._internal_error_count == 0


def test_reset_mirror_joins_worker_without_holding_worker_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """reset must not hold _mirror_worker_lock during join (D1 / a:41869)."""
    monkeypatch.setattr(esd, "_mirror_send_sync", lambda _line: None)
    _emit_sample()
    flush_mirror_worker_for_tests(timeout=2.0)
    worker = esd._mirror_worker_thread
    assert worker is not None
    assert worker.is_alive()
    started = time.monotonic()
    reset_mirror_state_for_tests()
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"reset blocked {elapsed:.2f}s (join under lock?)"
    assert not worker.is_alive()


def test_reset_stops_every_live_mirror_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """A replaced worker must not consume the only shutdown meant for join.

    ``test_fork_pid_change_starts_fresh_mirror_worker`` leaves the previous
    thread blocked on the same queue. One shutdown wakes that thread, and the
    thread ``reset`` joins stays in ``get`` until ``join`` times out.
    """
    monkeypatch.setattr(esd, "_mirror_send_sync", lambda _line: None)
    pid = os.getpid()
    monkeypatch.setattr(esd, "_getpid", lambda: pid)
    _emit_sample()
    flush_mirror_worker_for_tests(timeout=2.0)
    monkeypatch.setattr(esd, "_getpid", lambda: pid + 1)
    _emit_sample()
    flush_mirror_worker_for_tests(timeout=2.0)
    alive_before = [
        thread
        for thread in threading.enumerate()
        if thread.name == "cdp-skill-delivery-mirror" and thread.is_alive()
    ]
    assert len(alive_before) >= 2
    reset_mirror_state_for_tests()
    alive_after = [
        thread.name
        for thread in threading.enumerate()
        if thread.name == "cdp-skill-delivery-mirror" and thread.is_alive()
    ]
    assert alive_after == [], f"mirror threads still alive: {alive_after}"


def test_flush_sentinel_is_not_dropped_when_queue_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flush must return because the worker observed the marker, not at timeout.

    The marker used to be discarded on ``queue.Full`` while the worker was
    blocked in send, and ``done.wait`` then ran until its timeout.
    """
    started = threading.Event()
    release = threading.Event()

    def _block(_line: object) -> None:
        started.set()
        release.wait(timeout=2.0)

    monkeypatch.setattr(esd, "_mirror_send_sync", _block)
    esd._mirror_to_event_service(
        cdp_skill_delivery_attested(
            ok=True, attached=[], inlined=[], undelivered=[]
        )
    )
    assert started.wait(timeout=1.0)
    filler = b'{"signal":"x"}\n'
    for _ in range(esd._MIRROR_QUEUE_MAXSIZE):
        esd._mirror_queue.put_nowait(filler)

    def _release_later() -> None:
        time.sleep(0.05)
        release.set()

    releaser = threading.Thread(target=_release_later, daemon=True)
    releaser.start()
    started_flush = time.monotonic()
    flush_mirror_worker_for_tests(timeout=3.0)
    elapsed = time.monotonic() - started_flush
    releaser.join(timeout=1.0)
    assert elapsed < 1.0, f"flush blocked {elapsed:.2f}s (sentinel dropped?)"


def test_flush_does_not_wait_when_mirror_worker_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flush must not burn its timeout when worker start raises.

    The fixture used to call ``done.wait`` after ``_start_mirror_thread``
    refused the thread, so teardown sat until ``timeout`` with no worker.
    """

    def _boom(_thread: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(esd, "_start_mirror_thread", _boom)
    event = emit_skill_delivery_attested(
        ok=True,
        attached=["x"],
        inlined=[],
        undelivered=[],
    )
    assert event is not None
    started = time.monotonic()
    flush_mirror_worker_for_tests(timeout=2.0)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"flush blocked {elapsed:.2f}s (no worker)"


def test_first_warning_emits_inside_initial_300s_window(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC5: absent _last_warning_at must not suppress the first failure warning."""
    caplog.set_level(logging.WARNING, logger="claude_bundles.events_skill_delivery")
    monkeypatch.setattr(esd, "_now", lambda: 10.0)
    esd._record_send_failure("tcp", "127.0.0.1:7101", ConnectionRefusedError("offline"))
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "mirror failed" in r.message
    ]
    assert len(warnings) == 1
    assert "failures=1" in warnings[0].message

    esd._record_send_failure("tcp", "127.0.0.1:7101", ConnectionRefusedError("offline"))
    assert len([r for r in caplog.records if "mirror failed" in r.message]) == 1

    monkeypatch.setattr(esd, "_now", lambda: 311.0)
    esd._record_send_failure("tcp", "127.0.0.1:7101", ConnectionRefusedError("offline"))
    assert len([r for r in caplog.records if "mirror failed" in r.message]) == 2


def test_enqueue_sentinel_payload_eviction_counts_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1 (a): evicting a payload line during sentinel enqueue counts a drop."""
    reset_mirror_state_for_tests()
    key = _seed_warning_throttle(monkeypatch)
    fails, drops = esd._warning_counts.get(key, (0, 0))
    baseline_q = esd._queue_drop_count
    filler = b'{"signal":"payload"}\n'
    for _ in range(esd._MIRROR_QUEUE_MAXSIZE):
        esd._mirror_queue.put_nowait(filler)
    assert esd._enqueue_mirror_sentinel(_MirrorFlush()) is True
    assert esd._queue_drop_count == baseline_q + 1
    fails2, drops2 = esd._warning_counts.get(key, (0, 0))
    assert (fails2, drops2) == (fails, drops + 1)


def test_enqueue_sentinel_rotation_does_not_count_drop() -> None:
    """AC1 (b): rotating sentinels must not touch drop counters."""
    reset_mirror_state_for_tests()
    try:
        key = esd._mirror_transport_target()
    except Exception:
        key = ("uds", "unknown")
    baseline_q = esd._queue_drop_count
    baseline_c = esd._circuit_skip_count
    wc_before = esd._warning_counts.get(key, (0, 0))
    for _ in range(esd._MIRROR_QUEUE_MAXSIZE):
        esd._mirror_queue.put_nowait(_MirrorFlush())
    assert esd._enqueue_mirror_sentinel(_MirrorFlush()) is False
    assert esd._queue_drop_count == baseline_q
    assert esd._circuit_skip_count == baseline_c
    assert esd._warning_counts.get(key, (0, 0)) == wc_before


def test_exit_drain_flushes_queued_lines(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC8 (a): exit drain sends queued payload lines in order."""
    reset_mirror_state_for_tests()
    caplog.set_level(logging.WARNING)
    sent: list[bytes] = []
    monkeypatch.setattr(esd, "_mirror_send_sync", sent.append)
    lines = [b'{"a":1}\n', b'{"b":2}\n', b'{"c":3}\n']
    for line in lines:
        esd._mirror_queue.put_nowait(line)
    assert drain_mirror_queue_at_exit() == (3, 0)
    assert sent == lines
    assert not [
        r for r in caplog.records if "mirror exit drain" in r.message
    ]


def test_exit_drain_breaker_open_skips_and_summarizes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC8 (b): open breaker counts circuit skips and emits one exit summary."""
    reset_mirror_state_for_tests()
    key = _seed_warning_throttle(monkeypatch)
    caplog.set_level(logging.WARNING)
    esd._circuit_open_until = esd._now() + 30.0
    for _ in range(3):
        esd._mirror_queue.put_nowait(b"x\n")
    assert drain_mirror_queue_at_exit() == (0, 3)
    assert esd._circuit_skip_count == 3
    assert esd._warning_counts[key] == (0, 3)
    summaries = [
        r for r in caplog.records if "mirror exit drain" in r.message
    ]
    assert len(summaries) == 1


def test_exit_drain_send_failure_opens_breaker_for_rest(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC8 (c): one failed send opens breaker; later lines are circuit drops."""
    reset_mirror_state_for_tests()
    key = _seed_warning_throttle(monkeypatch)
    caplog.set_level(logging.WARNING)
    attempts: list[int] = []

    def _fail(_line: bytes) -> None:
        attempts.append(1)
        raise OSError("offline")

    monkeypatch.setattr(esd, "_mirror_send_sync", _fail)
    for _ in range(3):
        esd._mirror_queue.put_nowait(b"x\n")
    assert drain_mirror_queue_at_exit() == (0, 3)
    assert len(attempts) == 1
    assert esd._circuit_skip_count == 2
    assert esd._warning_counts[key] == (1, 2)
    assert len([r for r in caplog.records if "mirror exit drain" in r.message]) == 1


def test_exit_drain_deadline_drops_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC8 (d): after deadline, payload lines count as queue drops."""
    reset_mirror_state_for_tests()
    clock = [0.0]
    monkeypatch.setattr(esd, "_now", lambda: clock[0])
    sent: list[bytes] = []

    def _send_and_advance(line: bytes) -> None:
        sent.append(line)
        clock[0] = 2.0

    monkeypatch.setattr(esd, "_mirror_send_sync", _send_and_advance)
    for line in (b"one\n", b"two\n", b"three\n"):
        esd._mirror_queue.put_nowait(line)
    assert drain_mirror_queue_at_exit(timeout=1.0) == (1, 2)
    assert len(sent) == 1
    assert esd._queue_drop_count == 2


def test_exit_drain_handles_sentinels_without_counting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC8 (e): flush/shutdown markers are not payload; flush waiters wake."""
    reset_mirror_state_for_tests()
    baseline_q = esd._queue_drop_count
    baseline_c = esd._circuit_skip_count
    flush = _MirrorFlush()
    monkeypatch.setattr(esd, "_mirror_send_sync", lambda _line: None)
    esd._mirror_queue.put_nowait(flush)
    esd._mirror_queue.put_nowait(esd._MirrorShutdown())
    esd._mirror_queue.put_nowait(b"only\n")
    assert drain_mirror_queue_at_exit() == (1, 0)
    assert flush.done.is_set()
    assert esd._queue_drop_count == baseline_q
    assert esd._circuit_skip_count == baseline_c


def test_exit_drain_atexit_subprocess_flushes_lines() -> None:
    """AC8 (f): atexit hook drains queued lines in a real child process."""
    script = """
import os
from claude_bundles import events_skill_delivery as esd

path = os.environ["ESD_OUT"]

def capture(line):
    with open(path, "ab") as handle:
        handle.write(line)

esd._mirror_send_sync = capture
esd._mirror_queue.put_nowait(b"line-one\\n")
esd._mirror_queue.put_nowait(b"line-two\\n")
"""
    with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
        out_path = handle.name
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["ESD_OUT"] = out_path
    env["PYTHONPATH"] = os.pathsep.join(
        [str(repo_root / "libs"), str(repo_root), env.get("PYTHONPATH", "")]
    ).strip(os.pathsep)
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            timeout=30,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
        )
        assert completed.returncode == 0, completed.stderr
        payload = Path(out_path).read_bytes()
        assert payload.count(b"\n") == 2
    finally:
        os.unlink(out_path)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork unavailable")
def test_fork_child_emit_mirrors_after_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: forked child emits and drains via a stubbed send."""
    monkeypatch.setattr(esd, "_mirror_send_sync", lambda _line: None)
    esd._ensure_mirror_worker()
    assert esd._mirror_state_lock.acquire(blocking=False)
    child = os.fork()
    if child == 0:
        try:
            sent: list[bytes] = []
            esd._mirror_send_sync = lambda line: sent.append(line)
            emit_skill_delivery_attested(
                ok=True,
                attached=["fork-child"],
                inlined=[],
                undelivered=[],
            )
            flush_mirror_worker_for_tests(timeout=2.0)
            assert len(sent) >= 1
            assert b"cdp.skill.delivery_attested" in sent[0]
            os._exit(0)
        except Exception:
            os._exit(1)
    assert child > 0
    esd._mirror_state_lock.release()
    _, status = os.waitpid(child, 0)
    assert os.WEXITSTATUS(status) == 0


def test_reset_requeues_shutdown_when_join_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC4: shutdown survives join timeout while send is blocked."""
    release = threading.Event()

    def _block(_line: object) -> None:
        release.wait(timeout=2.0)

    monkeypatch.setattr(esd, "_mirror_send_sync", _block)
    monkeypatch.setattr(esd, "_RESET_JOIN_SECONDS", 0.05)
    esd._mirror_to_event_service(
        cdp_skill_delivery_attested(
            ok=True, attached=[], inlined=[], undelivered=[]
        )
    )
    time.sleep(0.05)
    reset_mirror_state_for_tests()
    release.set()
    time.sleep(0.2)
    alive = [
        t.name
        for t in threading.enumerate()
        if t.name == esd._MIRROR_WORKER_NAME and t.is_alive()
    ]
    assert alive == []
