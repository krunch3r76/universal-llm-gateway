"""Per-CDP-target writer lock — one mutating actuation at a time per targetId."""

from __future__ import annotations

import asyncio
import fcntl
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

_INPROC: dict[str, asyncio.Lock] = {}


class TargetLockBusyError(Exception):
    """Another writer holds the target lock."""


def lock_run_dir() -> Path:
    root = os.environ.get("ULG_WEB_TAB_LOCK_DIR", "").strip()
    if not root:
        runtime = os.environ.get("XDG_RUNTIME_DIR", "").strip()
        root = (
            f"{runtime}/universal-web-tab-locks"
            if runtime
            else "/tmp/universal-web-tab-locks"
        )
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_token(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "").strip())[:120]
    return text or "default"


def _lock_path(target_id: str) -> Path:
    return lock_run_dir() / f"{_safe_token(target_id)}.target.lock"


class _FcntlFileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fh: int | None = None

    def acquire(self, *, blocking: bool = True) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o666)
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(fd, flags)
        except BlockingIOError:
            os.close(fd)
            return False
        except OSError:
            os.close(fd)
            raise
        self._fh = fd
        return True

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            os.close(self._fh)
            self._fh = None


def _inproc(target_id: str) -> asyncio.Lock:
    key = _safe_token(target_id)
    return _INPROC.setdefault(key, asyncio.Lock())


@asynccontextmanager
async def cdp_open_bind_lock(cdp_url: str) -> AsyncIterator[None]:
    """Serialize open/bind for one CDP endpoint (avoid duplicate background tabs)."""
    key = _safe_token(cdp_url or "default")
    async with _inproc(f"open:{key}"):
        lock = _FcntlFileLock(_lock_path(f"open-{key}"))
        acquired = await asyncio.to_thread(lock.acquire, blocking=False)
        if not acquired:
            raise TargetLockBusyError(f"open:{key}")
        try:
            yield
        finally:
            await asyncio.to_thread(lock.release)


@asynccontextmanager
async def target_write_lock(target_id: str) -> AsyncIterator[None]:
    """Serialize paste/submit/bind mutations for one CDP target."""
    tid = str(target_id or "").strip()
    if not tid:
        raise ValueError("target_id required for target_write_lock")
    async with _inproc(tid):
        lock = _FcntlFileLock(_lock_path(tid))
        acquired = await asyncio.to_thread(lock.acquire, blocking=False)
        if not acquired:
            raise TargetLockBusyError(tid)
        try:
            yield
        finally:
            await asyncio.to_thread(lock.release)
