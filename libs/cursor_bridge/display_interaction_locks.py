"""Cross-process locks for display-chat paste (fcntl under a run dir).

Per-(display, agent_id) serializes one agent's paste end-to-end. The
per-display lock wraps the full switch-type-submit-readback-restore span so
one shared Grok composer is never driven by two agents at once. Parallel pastes
apply only across different displays.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path

_DISPLAY_SWITCH_DEPTH: ContextVar[int] = ContextVar("display_switch_depth", default=0)

_INPROC_AGENT: dict[tuple[str, str], asyncio.Lock] = {}
_INPROC_SWITCH: dict[str, asyncio.Lock] = {}


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


def _inproc_agent(display: str, agent_id: str) -> asyncio.Lock:
    key = (_safe_token(display), _safe_token(agent_id))
    return _INPROC_AGENT.setdefault(key, asyncio.Lock())


def _inproc_switch(display: str) -> asyncio.Lock:
    key = _safe_token(display)
    return _INPROC_SWITCH.setdefault(key, asyncio.Lock())


class _FcntlFileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o666)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            os.close(fd)
            raise
        self._fh = fd

    def release(self) -> None:
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
        await asyncio.to_thread(lock.acquire)
        try:
            yield
        finally:
            await asyncio.to_thread(lock.release)


@asynccontextmanager
async def display_switch_lock(display: str) -> AsyncIterator[None]:
    """Hold for switch, paste, readback, and restore on one display (reentrant)."""
    if _DISPLAY_SWITCH_DEPTH.get() > 0:
        yield
        return
    async with _inproc_switch(display):
        lock = _FcntlFileLock(_lock_path(display, kind="switch"))
        await asyncio.to_thread(lock.acquire)
        token = _DISPLAY_SWITCH_DEPTH.set(1)
        try:
            yield
        finally:
            _DISPLAY_SWITCH_DEPTH.reset(token)
            await asyncio.to_thread(lock.release)


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
