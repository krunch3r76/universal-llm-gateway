"""Cross-process target_write_lock — queue, timeout, cancel, holder death."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import signal
import time
from typing import Any

import pytest

from web_tab.target_lock import (
    TargetLockBusyError,
    _lock_path,
    read_lock_holder_metadata,
    target_write_lock,
)


def _holder_entry(
    lock_dir: str,
    target_id: str,
    ready: mp.synchronize.Event,
    release: mp.synchronize.Event,
    result: mp.Queue[Any],
) -> None:
    os.environ["ULG_WEB_TAB_LOCK_DIR"] = lock_dir

    async def run() -> None:
        try:
            async with target_write_lock(target_id, op="hold"):
                ready.set()
                while not release.is_set():
                    await asyncio.sleep(0.02)
            result.put({"ok": True, "released": True})
        except Exception as exc:  # noqa: BLE001 — child reports failure to parent
            result.put({"ok": False, "error": repr(exc)})

    asyncio.run(run())


def _waiter_entry(
    lock_dir: str,
    target_id: str,
    start: mp.synchronize.Event,
    done: mp.Queue[Any],
) -> None:
    os.environ["ULG_WEB_TAB_LOCK_DIR"] = lock_dir

    async def run() -> None:
        start.wait()
        try:
            async with target_write_lock(target_id, op="waiter"):
                done.put({"ok": True, "acquired": True})
        except Exception as exc:  # noqa: BLE001
            done.put({"ok": False, "error": repr(exc)})

    asyncio.run(run())


@pytest.fixture
def lock_dir(tmp_path: pytest.TempPathFactory) -> str:
    path = tmp_path / "locks"
    path.mkdir()
    return str(path)


@pytest.mark.asyncio
async def test_two_process_waiter_runs_after_release(lock_dir: str) -> None:
    target = "proc-fifo-target"
    ready = mp.Event()
    release = mp.Event()
    holder_result: mp.Queue[Any] = mp.Queue()
    waiter_done: mp.Queue[Any] = mp.Queue()
    start = mp.Event()

    holder = mp.Process(
        target=_holder_entry,
        args=(lock_dir, target, ready, release, holder_result),
    )
    waiter = mp.Process(
        target=_waiter_entry,
        args=(lock_dir, target, start, waiter_done),
    )
    holder.start()
    waiter.start()
    assert ready.wait(timeout=5.0)
    start.set()
    time.sleep(0.3)
    assert waiter_done.empty()
    release.set()
    holder.join(timeout=5.0)
    waiter.join(timeout=5.0)
    assert holder.exitcode == 0
    assert waiter.exitcode == 0
    out = waiter_done.get(timeout=1.0)
    assert out.get("acquired") is True


@pytest.mark.asyncio
async def test_sigkill_holder_waiter_acquires_before_deadline(
    lock_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = "sigkill-target"
    ready = mp.Event()
    release = mp.Event()
    holder_result: mp.Queue[Any] = mp.Queue()

    holder = mp.Process(
        target=_holder_entry,
        args=(lock_dir, target, ready, release, holder_result),
    )
    holder.start()
    assert ready.wait(timeout=5.0)
    os.kill(holder.pid, signal.SIGKILL)
    holder.join(timeout=5.0)
    assert holder.exitcode is not None

    monkeypatch.setenv("ULG_WEB_TAB_LOCK_DIR", lock_dir)
    async with target_write_lock(target, timeout_s=5.0, op="after-kill"):
        pass


@pytest.mark.asyncio
async def test_cancelled_waiter_releases_fd_and_lock_is_acquirable(
    lock_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = "cancel-target"
    ready = mp.Event()
    release = mp.Event()
    holder_result: mp.Queue[Any] = mp.Queue()

    holder = mp.Process(
        target=_holder_entry,
        args=(lock_dir, target, ready, release, holder_result),
    )
    holder.start()
    assert ready.wait(timeout=5.0)

    monkeypatch.setenv("ULG_WEB_TAB_LOCK_DIR", lock_dir)

    async def wait_for_lock() -> None:
        async with target_write_lock(target, timeout_s=30.0):
            pass

    task = asyncio.create_task(wait_for_lock())
    await asyncio.sleep(0.15)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    release.set()
    holder.join(timeout=5.0)
    async with target_write_lock(target, timeout_s=2.0):
        pass


@pytest.mark.asyncio
async def test_timeout_includes_holder_fields_and_later_waiter_acquires(
    lock_dir: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = "timeout-target"
    ready = mp.Event()
    release = mp.Event()
    holder_result: mp.Queue[Any] = mp.Queue()

    holder = mp.Process(
        target=_holder_entry,
        args=(lock_dir, target, ready, release, holder_result),
    )
    holder.start()
    assert ready.wait(timeout=5.0)

    monkeypatch.setenv("ULG_WEB_TAB_LOCK_DIR", lock_dir)
    with pytest.raises(TargetLockBusyError) as exc:
        async with target_write_lock(target, timeout_s=0.25):
            pass
    err = exc.value
    assert err.target_id == target
    assert err.waited_s is not None and err.waited_s >= 0.2
    assert err.holder.get("pid") == holder.pid
    assert err.holder.get("op") == "hold"
    assert err.holder.get("started_at")

    release.set()
    holder.join(timeout=5.0)
    async with target_write_lock(target, timeout_s=2.0):
        meta = read_lock_holder_metadata(_lock_path(target))
        assert meta.get("pid") == os.getpid()


@pytest.mark.asyncio
async def test_zero_timeout_attempts_flock_once(
    lock_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = "zero-timeout-target"
    ready = mp.Event()
    release = mp.Event()
    holder = mp.Process(
        target=_holder_entry,
        args=(lock_dir, target, ready, release, mp.Queue()),
    )
    holder.start()
    assert ready.wait(timeout=5.0)
    monkeypatch.setenv("ULG_WEB_TAB_LOCK_DIR", lock_dir)
    with pytest.raises(TargetLockBusyError):
        async with target_write_lock(target, timeout_s=0.0):
            pass
    release.set()
    holder.join(timeout=5.0)
