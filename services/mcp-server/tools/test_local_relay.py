from __future__ import annotations

import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from tools import _local_relay as relay_mod
from tools._local_relay import (
    RelayCapacityError,
    _request_with_wall_clock,
    _reset_orphan_workers_for_tests,
    relay,
    resolve_timeout,
)


@pytest.fixture(autouse=True)
def _clear_orphan_workers() -> None:
    _reset_orphan_workers_for_tests()


def test_email_pull_uses_extended_timeout() -> None:
    assert resolve_timeout("email-bridge", "POST", "/pull") == 120.0


def test_local_relay_uses_default_timeout_for_unlisted_routes() -> None:
    assert resolve_timeout("email-bridge", "GET", "/status") == 30.0


def test_review_extract_parameterized_route_uses_long_timeout() -> None:
    assert resolve_timeout("email-bridge", "POST", "/review/<msg-id>/extract") == 200.0


def test_review_dismiss_stays_on_default_timeout() -> None:
    assert resolve_timeout("email-bridge", "POST", "/review/<msg-id>/dismiss") == 30.0


def test_agent_bus_wait_suffix_uses_75s_budget() -> None:
    assert (
        resolve_timeout(
            "agent-bus",
            "GET",
            "/threads/4889/wait?after_turn=1&wait=60&completion=first_reply_from",
        )
        == 75.0
    )


def _relay_call_kwargs(**overrides: Any) -> dict[str, Any]:
    base = {
        "service_url": "unix:///tmp/agent-bus.sock",
        "request_timeout": 30.0,
        "method": "GET",
        "path": "/threads/1/wait",
        "json_body": None,
        "headers": {},
        "wall_clock_s": 0.2,
        "enforce_orphan_cap": True,
    }
    base.update(overrides)
    return base


def test_request_with_wall_clock_aborts_hanging_request() -> None:
    client = MagicMock(spec=httpx.Client)

    def _hang(*_a: Any, **_k: Any) -> httpx.Response:
        time.sleep(2.0)
        raise AssertionError("hang should have been aborted")

    client.request.side_effect = _hang

    t0 = time.monotonic()
    with (
        patch("tools._local_relay.make_sync_client", return_value=client),
        pytest.raises(FuturesTimeoutError),
    ):
        _request_with_wall_clock(**_relay_call_kwargs())
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0
    client.close.assert_not_called()


def test_request_with_wall_clock_times_out_when_close_hangs() -> None:
    client = MagicMock(spec=httpx.Client)
    response = MagicMock(spec=httpx.Response)

    def _fast_request(*_a: Any, **_k: Any) -> httpx.Response:
        return response

    def _slow_close() -> None:
        time.sleep(2.0)

    client.request.side_effect = _fast_request
    client.close.side_effect = _slow_close

    t0 = time.monotonic()
    with (
        patch("tools._local_relay.make_sync_client", return_value=client),
        pytest.raises(FuturesTimeoutError),
    ):
        _request_with_wall_clock(**_relay_call_kwargs())
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0
    with relay_mod._orphan_lock:
        assert len(relay_mod._orphan_workers) == 1
        assert relay_mod._orphan_workers[0].is_alive()


def test_request_with_wall_clock_close_failure_preserves_request_result() -> None:
    client = MagicMock(spec=httpx.Client)
    response = MagicMock(spec=httpx.Response)

    client.request.return_value = response
    client.close.side_effect = RuntimeError("close failed")

    with patch("tools._local_relay.make_sync_client", return_value=client):
        result = _request_with_wall_clock(**_relay_call_kwargs(wall_clock_s=1.0))

    assert result is response


def test_request_with_wall_clock_capacity_exhaustion_fails_fast() -> None:
    client = MagicMock(spec=httpx.Client)

    def _hang(*_a: Any, **_k: Any) -> httpx.Response:
        time.sleep(60.0)
        raise AssertionError("unreachable")

    client.request.side_effect = _hang

    with patch("tools._local_relay.make_sync_client", return_value=client):
        for _ in range(4):
            with pytest.raises(FuturesTimeoutError):
                _request_with_wall_clock(**_relay_call_kwargs(wall_clock_s=0.05))

        t0 = time.monotonic()
        with pytest.raises(RelayCapacityError):
            _request_with_wall_clock(**_relay_call_kwargs(wall_clock_s=0.05))
        assert time.monotonic() - t0 < 0.5


def test_concurrent_orphan_capacity_cannot_exceed_four() -> None:
    client = MagicMock(spec=httpx.Client)
    start_gate = threading.Barrier(4)

    def _hang_after_gate(*_a: Any, **_k: Any) -> httpx.Response:
        start_gate.wait(timeout=2.0)
        time.sleep(60.0)
        raise AssertionError("unreachable")

    client.request.side_effect = _hang_after_gate

    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def _attempt() -> None:
        try:
            _request_with_wall_clock(**_relay_call_kwargs(wall_clock_s=0.15))
            with outcomes_lock:
                outcomes.append("ok")
        except FuturesTimeoutError:
            with outcomes_lock:
                outcomes.append("timeout")
        except RelayCapacityError:
            with outcomes_lock:
                outcomes.append("capacity")

    with patch("tools._local_relay.make_sync_client", return_value=client):
        threads = [threading.Thread(target=_attempt) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)

    assert outcomes.count("timeout") == 4
    assert outcomes.count("capacity") == 1
    assert outcomes.count("ok") == 0
    assert relay_mod._orphan_slot_semaphore._value == 0


def test_expired_orphan_slot_is_reclaimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slot held past the lifetime cap is reclaimed so /wait doesn't wedge."""
    client = MagicMock(spec=httpx.Client)

    def _hang(*_a: Any, **_k: Any) -> httpx.Response:
        time.sleep(60.0)
        raise AssertionError("unreachable")

    client.request.side_effect = _hang

    with patch("tools._local_relay.make_sync_client", return_value=client):
        # Fill all four slots at the normal lifetime — no reclaim yet.
        for _ in range(4):
            with pytest.raises(FuturesTimeoutError):
                _request_with_wall_clock(**_relay_call_kwargs(wall_clock_s=0.05))
        assert relay_mod._orphan_slot_semaphore._value == 0

        # Force the held slots past their lifetime; the next acquire reclaims
        # them, so the wait admits and times out rather than raising
        # RelayCapacityError (the pre-fix wedge).
        monkeypatch.setattr(relay_mod, "_ORPHAN_MAX_LIFETIME_S", 0.0)
        with pytest.raises(FuturesTimeoutError):
            _request_with_wall_clock(**_relay_call_kwargs(wall_clock_s=0.05))


def test_completed_orphan_worker_is_forgotten() -> None:
    """A timed-out worker that later completes releases its slot and is dropped."""
    client = MagicMock(spec=httpx.Client)
    response = MagicMock(spec=httpx.Response)
    gate = threading.Event()

    def _blocked_request(*_a: Any, **_k: Any) -> httpx.Response:
        gate.wait(timeout=5.0)
        return response

    client.request.side_effect = _blocked_request

    with patch("tools._local_relay.make_sync_client", return_value=client):
        with pytest.raises(FuturesTimeoutError):
            _request_with_wall_clock(**_relay_call_kwargs(wall_clock_s=0.1))
        with relay_mod._orphan_lock:
            assert len(relay_mod._orphan_workers) == 1
        # Let the worker finish; its finally releases the slot (value back to 4).
        gate.set()
        for _ in range(50):
            if relay_mod._orphan_slot_semaphore._value == relay_mod._MAX_ORPHAN_WORKERS:
                break
            time.sleep(0.05)
        assert relay_mod._orphan_slot_semaphore._value == relay_mod._MAX_ORPHAN_WORKERS
        # A reclaim sweep forgets the now-dead worker without double-releasing.
        relay_mod._reclaim_expired_orphans()
        with relay_mod._orphan_lock:
            assert len(relay_mod._orphan_workers) == 0
        assert relay_mod._orphan_slot_semaphore._value == relay_mod._MAX_ORPHAN_WORKERS


def test_dead_orphan_worker_sweep_releases_slot_idempotently() -> None:
    """F4: reclaim of a dead worker still calls slot.release() (idempotent)."""
    slot = relay_mod._SlotToken(relay_mod._orphan_slot_semaphore)
    # Simulate a worker that died without running finally (permit still held).
    assert relay_mod._orphan_slot_semaphore.acquire(blocking=False)
    dead = threading.Thread(target=lambda: None, daemon=True)
    dead.start()
    dead.join(timeout=1.0)
    assert not dead.is_alive()
    with relay_mod._orphan_lock:
        relay_mod._orphan_workers.append(dead)
        relay_mod._orphan_meta[dead] = (time.monotonic(), slot)

    before = relay_mod._orphan_slot_semaphore._value
    relay_mod._reclaim_expired_orphans()
    assert relay_mod._orphan_slot_semaphore._value == before + 1
    with relay_mod._orphan_lock:
        assert dead not in relay_mod._orphan_workers
        assert dead not in relay_mod._orphan_meta
    # Second reclaim must not double-release.
    relay_mod._reclaim_expired_orphans()
    assert relay_mod._orphan_slot_semaphore._value == before + 1


def test_worker_start_failure_releases_orphan_slot() -> None:
    """F5: Thread.start() failure must not leak the acquired admission permit."""
    slot_holder: dict[str, Any] = {}

    real_acquire = relay_mod._acquire_orphan_slot

    def _acquire_and_track() -> relay_mod._SlotToken:
        token = real_acquire()
        slot_holder["token"] = token
        return token

    with (
        patch.object(relay_mod, "_acquire_orphan_slot", side_effect=_acquire_and_track),
        patch.object(
            threading.Thread,
            "start",
            side_effect=RuntimeError("thread start failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="thread start failed"):
            _request_with_wall_clock(**_relay_call_kwargs(wall_clock_s=0.1))

    token = slot_holder["token"]
    assert token._released is True
    assert relay_mod._orphan_slot_semaphore._value == relay_mod._MAX_ORPHAN_WORKERS


def test_relay_wall_clock_timeout_emits_failed_and_returns_error() -> None:
    hanging = MagicMock(spec=httpx.Client)

    def _hang(*_a: Any, **_k: Any) -> httpx.Response:
        time.sleep(2.0)
        raise AssertionError("unreachable")

    hanging.request.side_effect = _hang
    recorded: list[tuple[str, dict[str, Any]]] = []

    def _record(signal: str, **payload: Any) -> None:
        recorded.append((signal, payload))

    with (
        patch("tools._local_relay.make_sync_client", return_value=hanging),
        patch("tools._local_relay.record", side_effect=_record),
        patch("tools._local_relay.resolve_timeout", return_value=0.2),
    ):
        result = relay("agent-bus", "GET", "/threads/1/wait?wait=60")

    assert result == {"error": "Request to agent-bus timed out"}
    signals = [s for s, _ in recorded]
    assert "mcp.local.api.called" in signals
    assert "mcp.local.api.failed" in signals
    failed = next(p for s, p in recorded if s == "mcp.local.api.failed")
    assert failed["error"] == "wall_clock_timeout"


def test_relay_capacity_exhaustion_emits_failed_and_returns_error() -> None:
    hanging = MagicMock(spec=httpx.Client)

    def _hang(*_a: Any, **_k: Any) -> httpx.Response:
        time.sleep(60.0)
        raise AssertionError("unreachable")

    hanging.request.side_effect = _hang
    recorded: list[tuple[str, dict[str, Any]]] = []

    def _record(signal: str, **payload: Any) -> None:
        recorded.append((signal, payload))

    with (
        patch("tools._local_relay.make_sync_client", return_value=hanging),
        patch("tools._local_relay.record", side_effect=_record),
        patch("tools._local_relay.resolve_timeout", return_value=0.05),
    ):
        for _ in range(4):
            relay("agent-bus", "GET", "/threads/1/wait?wait=60")

        t0 = time.monotonic()
        result = relay("agent-bus", "GET", "/threads/1/wait?wait=60")
        elapsed = time.monotonic() - t0

    assert result == {"error": "Local relay capacity exhausted; retry later"}
    assert elapsed < 0.5
    failed_events = [p for s, p in recorded if s == "mcp.local.api.failed"]
    assert any(event["error"] == "relay_capacity_exhausted" for event in failed_events)
