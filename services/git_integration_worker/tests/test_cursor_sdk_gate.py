"""Tests for cursor-sdk capacity gate — thread-owned lease semantics.

1. The bridge command is a pure function of its arguments: two dispatches get
   distinct argv and the worker's os.environ is never written.
2. Timeout/orphan: slot is held until the orphan thread's finally fires.
"""

from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import Future

import pytest

from services.git_integration_worker.cursor_sdk_bridge_launch import (
    build_bridge_command,
)
from services.git_integration_worker.cursor_sdk_gate import (
    _STANDARD_GATE,
    acquire_sdk_dispatch_slot,
    release_sdk_dispatch_slot_sync,
)

# ---------------------------------------------------------------------------
# Test 1 — Command purity: no process env mutation, no shared state
# ---------------------------------------------------------------------------


def test_bridge_command_is_pure(tmp_path: pytest.FixtureDef) -> None:
    """Two dispatches yield distinct argv; os.environ is untouched by both."""
    import sys

    before = dict(os.environ)
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    argv_a = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=home_a,
        repo_venv=None,
        real_home=None,
        dispatch_id="disp-a",
    )
    argv_b = build_bridge_command(
        bridge_bin=sys.executable,
        dispatch_home=home_b,
        repo_venv=None,
        real_home=None,
        dispatch_id="disp-b",
    )
    assert dict(os.environ) == before, "build_bridge_command must not write os.environ"
    assert argv_a != argv_b
    assert f"HOME={home_a}" in argv_a and f"HOME={home_b}" in argv_b
    assert f"HOME={home_b}" not in argv_a


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
