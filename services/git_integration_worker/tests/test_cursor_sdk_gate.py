"""Tests for cursor-sdk capacity gate — thread-owned lease semantics.

Covers the four correctness properties from F3 (GPT-5.5 review, agent-bus:1804):
1. Bridge-env overlay does not mutate os.environ.
2. Concurrent threads see their own HOME overlay.
3. Thread reuse: overlay is absent after the context exits.
4. Timeout/orphan: slot is held until the orphan thread's finally fires.
"""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import Future

import pytest

from services.git_integration_worker.cursor_sdk_bridge_launch import (
    _dispatch_env,
    _dispatch_home_overlay,
)
from services.git_integration_worker.cursor_sdk_gate import (
    _STANDARD_GATE,
    acquire_sdk_dispatch_slot,
    release_sdk_dispatch_slot_sync,
)

# ---------------------------------------------------------------------------
# Test 1 — No process env mutation
# ---------------------------------------------------------------------------


def test_dispatch_home_overlay_no_environ_mutation(tmp_path: pytest.FixtureDef) -> None:
    """_dispatch_home_overlay must not mutate os.environ["HOME"]."""
    original_home = os.environ.get("HOME")
    fake_home = str(tmp_path / "private-home")

    with _dispatch_home_overlay(tmp_path / "private-home"):
        assert os.environ.get("HOME") == original_home, (
            "overlay must not write to os.environ"
        )
        assert getattr(_dispatch_env, "overrides", {}).get("HOME") == fake_home, (
            "overlay must be visible via thread-local"
        )

    assert os.environ.get("HOME") == original_home


# ---------------------------------------------------------------------------
# Test 2 — Concurrent thread isolation
# ---------------------------------------------------------------------------


def test_concurrent_thread_isolation(tmp_path: pytest.FixtureDef) -> None:
    """Two threads with different HOME overlays must see their own private HOME."""
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    seen: dict[str, str | None] = {}
    barrier = threading.Barrier(2)

    def thread_fn(name: str, home: object) -> None:
        with _dispatch_home_overlay(home):
            barrier.wait()  # both threads inside overlay simultaneously
            seen[name] = getattr(_dispatch_env, "overrides", {}).get("HOME")

    t1 = threading.Thread(target=thread_fn, args=("a", home_a))
    t2 = threading.Thread(target=thread_fn, args=("b", home_b))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert seen["a"] == str(home_a), "thread A must see its own HOME"
    assert seen["b"] == str(home_b), "thread B must see its own HOME"
    assert seen["a"] != seen["b"]


# ---------------------------------------------------------------------------
# Test 3 — Thread reuse cleanup
# ---------------------------------------------------------------------------


def test_thread_reuse_overlay_cleanup(tmp_path: pytest.FixtureDef) -> None:
    """After the overlay context exits the thread-local override must be absent."""
    home = tmp_path / "dispatch-home"
    seen_inside: list[str | None] = []
    seen_outside: list[str | None] = []

    def thread_fn() -> None:
        with _dispatch_home_overlay(home):
            seen_inside.append(
                (getattr(_dispatch_env, "overrides", None) or {}).get("HOME")
            )
        # After exit, prev=None is restored; overrides attr is set to None.
        seen_outside.append(
            (getattr(_dispatch_env, "overrides", None) or {}).get("HOME")
        )

    t = threading.Thread(target=thread_fn)
    t.start()
    t.join()

    assert seen_inside == [str(home)]
    assert seen_outside == [None], "override must be cleared after context exits"


# ---------------------------------------------------------------------------
# Test 4 — Timeout/orphan capacity: slot released only when orphan thread exits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_holds_slot_until_finally() -> None:
    """Slot must remain active while an orphan thread is running.

    Simulates the timeout path: the outer coroutine proceeds past the
    asyncio.wait timeout while the worker thread is still blocked.  The
    gate's active_count must stay at 1 until the worker's finally block
    fires release_sdk_dispatch_slot_sync.
    """
    loop = asyncio.get_running_loop()

    # Drain any stale gate state (other tests may have left it non-zero).
    for holder in list(_STANDARD_GATE.holders):
        await _STANDARD_GATE.force_release(holder)

    worker_started = asyncio.Event()
    worker_unblock: Future[None] = Future()
    slot_released = asyncio.Event()
    orphan_id = "test-orphan"

    def worker_thread() -> None:
        try:
            loop.call_soon_threadsafe(worker_started.set)
            worker_unblock.result(timeout=10.0)  # blocked — simulating long SDK run
        finally:
            release_sdk_dispatch_slot_sync(loop, dispatch_id=orphan_id)
            loop.call_soon_threadsafe(slot_released.set)

    await acquire_sdk_dispatch_slot(dispatch_id=orphan_id)
    assert _STANDARD_GATE.active_count == 1

    t = threading.Thread(target=worker_thread, daemon=True)
    t.start()
    await worker_started.wait()

    # Outer coroutine times out — slot still held by thread.
    assert _STANDARD_GATE.active_count == 1, "slot must remain active while thread runs"

    # Unblock the thread so its finally fires.
    worker_unblock.set_result(None)
    await asyncio.wait_for(slot_released.wait(), timeout=5.0)

    assert _STANDARD_GATE.active_count == 0, "slot must be released after thread finally fires"
    t.join(timeout=2.0)
