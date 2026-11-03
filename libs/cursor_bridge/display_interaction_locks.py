"""Cross-process locks for display-chat paste (fcntl under a run dir).

Per-(display, agent_id) serializes one agent's paste end-to-end. The
per-display lock wraps the full switch-type-submit-readback-restore span so
one shared Grok composer is never driven by two agents at once. Parallel pastes
apply only across different displays.

Two layers per lock: an in-process ``asyncio.Lock`` (FIFO among tasks of one
event loop, kept per running loop so several loops in one process never share
one) and an ``fcntl.flock`` on a file under :func:`lock_run_dir`, which is the
real mutual-exclusion boundary across loops, threads and processes.

The flock is taken with ``LOCK_NB`` in a polling loop on the event loop, not
with a blocking ``flock`` in a worker thread. A cancelled waiter therefore
closes its fd at the ``await asyncio.sleep`` and leaves nothing behind; a
blocking acquire in a thread would keep running after cancellation and hold
the lock with no owner to release it. Cost: up to ``_POLL_MAX_S`` extra
latency after a release, and no FIFO order across processes. There is no
built-in deadline (callers wait as before); ``asyncio.timeout`` / ``wait_for``
around the context manager is safe because cancellation is clean.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import re
import threading
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

_DISPLAY_SWITCH_DEPTH: ContextVar[int] = ContextVar("display_switch_depth", default=0)

# In-process locks are keyed by (running loop, key): an asyncio.Lock binds to the
# first loop that waits on it, so one shared across loops raises RuntimeError or
# strands waiters. Entries drop when the last holder or waiter exits (user count
# hits zero); a contended lock keeps its loop alive until then.
_INPROC_GUARD = threading.Lock()


@dataclass
class _InprocEntry:
    lock: asyncio.Lock
    users: int


_INPROC_AGENT: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, str], _InprocEntry]
] = weakref.WeakKeyDictionary()
_INPROC_SWITCH: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, _InprocEntry]
] = weakref.WeakKeyDictionary()

_POLL_INITIAL_S = 0.005
_POLL_MAX_S = 0.05


def lock_run_dir() -> Path:
    root = os.environ.get("ULG_DISPLAY_LOCK_DIR", "").strip()
    if not root:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        root = (
            f"{runtime}/universal-display-chat-locks"
            if runtime
            else "/tmp/universal-display-chat-locks"
        )
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_token(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip())[:120]
    return text or "default"


def _lock_path(display: str, *, kind: str, agent_id: str = "") -> Path:
    disp = _safe_token(str(display or "").strip().lstrip(":"))
    if kind == "switch":
        return lock_run_dir() / f"{disp}.switch.lock"
    aid = _safe_token(agent_id)
    return lock_run_dir() / f"{disp}.{aid}.agent.lock"


def _inproc_drop_agent(
    loop: asyncio.AbstractEventLoop, key: tuple[str, str]
) -> None:
    per_loop = _INPROC_AGENT.get(loop)
    if per_loop is None:
        return
    entry = per_loop.get(key)
    if entry is None:
        return
    entry.users -= 1
    if entry.users <= 0:
        del per_loop[key]
        if not per_loop:
            del _INPROC_AGENT[loop]


def _inproc_drop_switch(loop: asyncio.AbstractEventLoop, key: str) -> None:
    per_loop = _INPROC_SWITCH.get(loop)
    if per_loop is None:
        return
    entry = per_loop.get(key)
    if entry is None:
        return
    entry.users -= 1
    if entry.users <= 0:
        del per_loop[key]
        if not per_loop:
            del _INPROC_SWITCH[loop]


@asynccontextmanager
async def _inproc_agent(display: str, agent_id: str) -> AsyncIterator[None]:
    """Serialize coroutines on the running loop for one agent key."""
    key = (_safe_token(display), _safe_token(agent_id))
    loop = asyncio.get_running_loop()
    with _INPROC_GUARD:
        per_loop = _INPROC_AGENT.setdefault(loop, {})
        entry = per_loop.get(key)
        if entry is None:
            entry = _InprocEntry(lock=asyncio.Lock(), users=0)
            per_loop[key] = entry
        entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        with _INPROC_GUARD:
            _inproc_drop_agent(loop, key)


@asynccontextmanager
async def _inproc_switch(display: str) -> AsyncIterator[None]:
    """Serialize coroutines on the running loop for one display switch key."""
    key = _safe_token(display)
    loop = asyncio.get_running_loop()
    with _INPROC_GUARD:
        per_loop = _INPROC_SWITCH.setdefault(loop, {})
        entry = per_loop.get(key)
        if entry is None:
            entry = _InprocEntry(lock=asyncio.Lock(), users=0)
            per_loop[key] = entry
        entry.users += 1
    try:
        async with entry.lock:
            yield
    finally:
        with _INPROC_GUARD:
            _inproc_drop_switch(loop, key)


class _FcntlFileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: int | None = None

    async def acquire(self) -> None:
        """Take the exclusive flock, polling ``LOCK_NB`` until it is free.

        Cancellation (or any error) while waiting closes the fd before
        re-raising, so an abandoned wait never ends up holding the lock.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o666)
        delay = _POLL_INITIAL_S
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, _POLL_MAX_S)
        except BaseException:
            os.close(fd)
            raise
        self._fh = fd

    def release(self) -> None:
        """Drop the flock and close the fd (``LOCK_UN`` never blocks)."""
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            os.close(self._fh)
            self._fh = None


@asynccontextmanager
async def agent_paste_lock(display: str, agent_id: str) -> AsyncIterator[None]:
    """Hold for one agent paste; different agent_ids on the same display may overlap."""
    aid = str(agent_id or "").strip() or "anonymous"
    async with _inproc_agent(display, aid):
        lock = _FcntlFileLock(_lock_path(display, kind="agent", agent_id=aid))
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()


@asynccontextmanager
async def display_switch_lock(display: str) -> AsyncIterator[None]:
    """Hold for switch, paste, readback, and restore on one display (reentrant)."""
    if _DISPLAY_SWITCH_DEPTH.get() > 0:
        yield
        return
    async with _inproc_switch(display):
        lock = _FcntlFileLock(_lock_path(display, kind="switch"))
        await lock.acquire()
        token = _DISPLAY_SWITCH_DEPTH.set(1)
        try:
            yield
        finally:
            _DISPLAY_SWITCH_DEPTH.reset(token)
            lock.release()


def paste_lock_key(*, agent_id: str = "", thread: str = "") -> str:
    """Stable lock identity when only thread id is known (wake path)."""
    aid = str(agent_id or "").strip()
    if aid:
        return aid
    thread_s = str(thread or "").strip()
    return f"wake:{thread_s}" if thread_s else "anonymous"


__all__ = [
    "agent_paste_lock",
    "display_switch_lock",
    "lock_run_dir",
    "paste_lock_key",
]
