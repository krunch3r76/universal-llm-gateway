"""target_lock — cross-process queue, timeout, cancel, holder death, multi-loop."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
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


# --- Multi-loop and open/bind cancellation regressions ---------------------
#
# Each scenario runs in a child interpreter with a hard timeout: a regression
# strands a waiter on a foreign event loop or leaves a flock held, which would
# otherwise wedge the pytest process. The child starts with fresh module state,
# so locks created by other tests' event loops cannot mask or cause a failure.

_LIBS = Path(__file__).resolve().parents[1]
_CHILD_TIMEOUT_S = 30.0


def _run_child(script: str, lock_dir: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["ULG_WEB_TAB_LOCK_DIR"] = lock_dir
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(_LIBS), env.get("PYTHONPATH", "")) if p
    )
    try:
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=_CHILD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"child hung past {_CHILD_TIMEOUT_S}s: stdout={exc.stdout!r}")


def _assert_child_ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0 and "OK" in proc.stdout, (
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


_SEQUENTIAL_LOOPS_SCRIPT = """
    import asyncio
    from web_tab.target_lock import target_write_lock

    async def contend():
        order = []

        async def worker(name):
            async with target_write_lock("multi-loop-target", timeout_s=5.0, op=name):
                order.append(name)
                await asyncio.sleep(0.05)

        await asyncio.gather(worker("a"), worker("b"))
        assert sorted(order) == ["a", "b"], order

    # Each asyncio.run is a new loop; both contend the same target, so the
    # in-process lock has a waiter (and a loop-bound future) in each loop.
    asyncio.run(contend())
    asyncio.run(contend())
    print("OK", flush=True)
"""


def test_target_write_lock_contended_on_two_sequential_loops(lock_dir: str) -> None:
    """A target contended on one loop stays usable on a later loop (H3 shape)."""
    _assert_child_ok(_run_child(_SEQUENTIAL_LOOPS_SCRIPT, lock_dir))


_CONCURRENT_LOOPS_SCRIPT = """
    import asyncio, os, threading
    from web_tab.target_lock import target_write_lock

    target = "two-live-loops-target"
    a_holding = threading.Event()
    a_release = threading.Event()
    b_entered = threading.Event()
    errors = []

    def run_a():
        async def main():
            async with target_write_lock(target, timeout_s=5.0, op="a"):
                a_holding.set()
                while not a_release.is_set():
                    await asyncio.sleep(0.01)
        try:
            asyncio.run(main())
        except BaseException as exc:
            errors.append(("a", repr(exc)))

    def run_b():
        async def main():
            async with target_write_lock(target, timeout_s=10.0, op="b"):
                b_entered.set()
        try:
            asyncio.run(main())
        except BaseException as exc:
            errors.append(("b", repr(exc)))

    ta = threading.Thread(target=run_a, daemon=True)
    ta.start()
    assert a_holding.wait(5), "a never acquired"
    tb = threading.Thread(target=run_b, daemon=True)
    tb.start()
    if b_entered.wait(0.4):
        print("FAIL: b entered while a held the lock", flush=True)
        os._exit(3)
    a_release.set()
    ta.join(5)
    if not b_entered.wait(3.0):
        print(f"FAIL: b stranded after a released; errors={errors}", flush=True)
        os._exit(4)
    tb.join(5)
    if errors:
        print(f"FAIL: errors={errors}", flush=True)
        os._exit(5)
    print("OK", flush=True)
    os._exit(0)
"""


def test_target_write_lock_two_live_loops_exclude_and_hand_off(lock_dir: str) -> None:
    """Two loops in threads: the second waits while the first holds, then enters."""
    _assert_child_ok(_run_child(_CONCURRENT_LOOPS_SCRIPT, lock_dir))


_OPEN_BIND_CANCEL_SCRIPT = """
    import asyncio, fcntl, os, threading, time
    from web_tab import target_lock
    from web_tab.target_lock import _FcntlFileLock, _lock_path, _safe_token, cdp_open_bind_lock

    cdp = "http://127.0.0.1:9222"
    path = _lock_path(f"open-{_safe_token(cdp)}")
    entered = threading.Event()
    gate = threading.Event()
    orig = _FcntlFileLock.acquire

    def gated_acquire(self, *, blocking=True):
        # The flock is taken, then the result is held back until the caller
        # has been cancelled: the window between the syscall and the caller
        # observing its result.
        ok = orig(self, blocking=blocking)
        if not gate.is_set():
            entered.set()
            gate.wait(5)
        return ok

    _FcntlFileLock.acquire = gated_acquire

    def free_from_other_fd():
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)

    async def main():
        loop = asyncio.get_running_loop()

        async def opener():
            async with cdp_open_bind_lock(cdp):
                await asyncio.sleep(10)

        task = asyncio.create_task(opener())

        def canceller():
            assert entered.wait(5), "acquire never ran"
            loop.call_soon_threadsafe(task.cancel)
            time.sleep(0.2)
            gate.set()

        threading.Thread(target=canceller, daemon=True).start()
        try:
            await asyncio.wait_for(task, 5)
        except asyncio.CancelledError:
            pass
        # Give any worker thread time to finish its acquire.
        await asyncio.sleep(0.3)
        if not free_from_other_fd():
            print("FAIL: open/bind flock still held after cancelled opener", flush=True)
            os._exit(3)
        async with cdp_open_bind_lock(cdp):
            pass
        print("OK", flush=True)

    asyncio.run(main())
"""


def test_cdp_open_bind_lock_cancel_during_acquire_releases_flock(
    lock_dir: str,
) -> None:
    """Cancelling an opener while its flock acquire is in flight leaves no holder."""
    _assert_child_ok(_run_child(_OPEN_BIND_CANCEL_SCRIPT, lock_dir))
