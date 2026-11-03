"""Regression tests for display_interaction_locks: cancel safety and multi-loop use.

Each scenario runs in a child interpreter with a hard timeout. A regression in
either property hangs a worker thread (a leaked flock or a stranded asyncio
waiter); in the child that becomes a timeout and a failing assertion instead of
wedging the pytest process at interpreter shutdown.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_LIBS = Path(__file__).resolve().parents[2]
_CHILD_TIMEOUT_S = 20.0

# Child: another process holds the switch flock; a waiter in this process is
# cancelled while blocked on that flock; the other process releases; a fresh
# acquirer must then get the lock within a short deadline, and an independent
# process must see the lock free afterwards.
_CANCEL_SCRIPT = textwrap.dedent(
    """
    import asyncio, fcntl, os, subprocess, sys
    from cursor_bridge.display_interaction_locks import (
        agent_paste_lock, display_switch_lock, lock_run_dir,
    )

    HOLD = (
        "import fcntl,os,sys;"
        "fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o666);"
        "fcntl.flock(fd,fcntl.LOCK_EX);print('held',flush=True);"
        "sys.stdin.readline()"
    )

    def free_from_other_process(path):
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        except BlockingIOError:
            return False
        finally:
            os.close(fd)

    async def scenario(name, cm_factory, path):
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLD, path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        try:
            assert holder.stdout.readline().strip() == "held"
            waiter = asyncio.create_task(cm_factory().__aenter__())
            await asyncio.sleep(0.3)
            assert not waiter.done(), f"{name}: waiter got a held lock"
            waiter.cancel()
            try:
                await waiter
            except asyncio.CancelledError:
                pass
        finally:
            holder.stdin.write("\\n")
            holder.stdin.flush()
            holder.wait(5)

        async def second():
            async with cm_factory():
                return True

        try:
            await asyncio.wait_for(second(), 2.0)
        except TimeoutError:
            print(f"FAIL {name}: second acquirer timed out after cancel", flush=True)
            os._exit(3)
        if not free_from_other_process(path):
            print(f"FAIL {name}: lock still held after release", flush=True)
            os._exit(4)
        print(f"OK {name}", flush=True)

    async def main():
        d = lock_run_dir()
        await scenario(
            "switch", lambda: display_switch_lock("7"), str(d / "7.switch.lock")
        )
        await scenario(
            "agent", lambda: agent_paste_lock("7", "a1"), str(d / "7.a1.agent.lock")
        )

    asyncio.run(main())
    os._exit(0)
    """
)

# Child: two threads, each with its own asyncio.run loop, contend for the same
# agent and switch locks. Must finish with no RuntimeError, no hung thread, and
# never more than one holder of the switch lock at once (flock still serializes
# across loops after the in-process locks are split per loop).
_MULTILOOP_SCRIPT = textwrap.dedent(
    """
    import asyncio, os, threading
    from cursor_bridge.display_interaction_locks import (
        agent_paste_lock, display_switch_lock,
    )

    errors = []
    state = {"inside": 0, "max_inside": 0, "entries": 0}
    guard = threading.Lock()

    def run(tag):
        async def one():
            async with agent_paste_lock("8", "same-agent"):
                async with display_switch_lock("8"):
                    with guard:
                        state["inside"] += 1
                        state["entries"] += 1
                        state["max_inside"] = max(state["max_inside"], state["inside"])
                    await asyncio.sleep(0.01)
                    with guard:
                        state["inside"] -= 1

        async def main():
            await asyncio.gather(*(one() for _ in range(5)))

        try:
            asyncio.run(main())
        except BaseException as exc:
            errors.append(f"{tag}: {type(exc).__name__}: {exc}")

    # Run the pair twice so the second round reuses keys first seen by loops
    # that are now closed.
    for _round in range(2):
        threads = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(8)
        hung = sum(t.is_alive() for t in threads)
        if errors or hung:
            print(f"FAIL errors={errors} hung={hung}", flush=True)
            os._exit(3)
    if state["max_inside"] != 1 or state["entries"] != 20:
        print(f"FAIL state={state}", flush=True)
        os._exit(4)
    print(f"OK state={state}", flush=True)
    os._exit(0)
    """
)


def _run_child(script: str, lock_dir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["ULG_DISPLAY_LOCK_DIR"] = str(lock_dir)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(_LIBS), env.get("PYTHONPATH", "")) if p
    )
    try:
        return subprocess.run(
            [sys.executable, "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=_CHILD_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        pytest.fail(f"child hung past {_CHILD_TIMEOUT_S}s; stdout={out!r}")


def test_cancelled_flock_waiter_does_not_leak_lock(tmp_path: Path) -> None:
    """Cancel a waiter blocked on another process's flock; the next acquirer wins."""
    proc = _run_child(_CANCEL_SCRIPT, tmp_path)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr[-2000:])
    assert "OK switch" in proc.stdout
    assert "OK agent" in proc.stdout


def test_locks_usable_from_two_event_loops(tmp_path: Path) -> None:
    """Two threads each running asyncio.run share keys without hang or RuntimeError."""
    proc = _run_child(_MULTILOOP_SCRIPT, tmp_path)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr[-2000:])
    assert proc.stdout.startswith("OK")
