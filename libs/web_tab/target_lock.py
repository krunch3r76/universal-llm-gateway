"""Per-CDP-target writer lock — one mutating actuation at a time per targetId."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_INPROC: dict[str, asyncio.Lock] = {}


class TargetLockBusyError(Exception):
    """Another writer holds the target lock (``code`` = ``target_lock_busy``)."""

    code = "target_lock_busy"

    def __init__(
        self,
        target_id: str,
        *,
        waited_s: float | None = None,
        holder: dict[str, Any] | None = None,
    ) -> None:
        self.target_id = target_id
        self.waited_s = waited_s
        self.holder = dict(holder or {})
        parts = [f"target_id={target_id!r}"]
        if waited_s is not None:
            parts.append(f"waited_s={waited_s:.3f}")
        for key in ("pid", "op", "started_at"):
            val = self.holder.get(key)
            if val is not None and str(val) != "":
                parts.append(f"holder_{key}={val!r}")
        super().__init__("; ".join(parts))


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


def _default_timeout_s() -> float:
    raw = os.environ.get("ULG_WEB_TAB_LOCK_TIMEOUT_S", "120").strip()
    try:
        return float(raw) if raw else 120.0
    except ValueError:
        return 120.0


def read_lock_holder_metadata(path: Path) -> dict[str, Any]:
    """Best-effort read of holder fields from the lock file (may be stale)."""
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_holder_metadata(fd: int, *, op: str) -> None:
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "op": op or "",
            "started_at": datetime.now(UTC).isoformat(),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, payload)


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


async def _acquire_cross_process_lock(
    path: Path,
    *,
    target_id: str,
    deadline: float,
    op: str,
) -> int:
    """Acquire ``LOCK_EX`` with non-blocking retries until ``deadline`` (asyncio loop time)."""
    loop = asyncio.get_running_loop()
    delay = 0.05
    started = loop.time()
    fd: int | None = None
    try:
        while True:
            now = loop.time()
            if now >= deadline:
                waited = now - started
                holder = read_lock_holder_metadata(path)
                if fd is not None:
                    os.close(fd)
                    fd = None
                raise TargetLockBusyError(
                    target_id,
                    waited_s=waited,
                    holder=holder,
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o666)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                fd = None
                remaining = max(0.0, deadline - loop.time())
                await asyncio.sleep(min(delay, remaining))
                delay = min(delay * 2, 1.0)
                continue
            except asyncio.CancelledError:
                os.close(fd)
                raise
            _write_holder_metadata(fd, op=op)
            return fd
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _release_cross_process_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


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
async def target_write_lock(
    target_id: str,
    *,
    timeout_s: float | None = None,
    op: str = "",
) -> AsyncIterator[None]:
    """Serialize paste/submit/bind mutations for one CDP target.

    Holds the per-process ``asyncio.Lock`` for the whole wait and critical section
    (FIFO among coroutines in this process). Cross-process exclusion uses ``flock``
    on one file per target with non-blocking retries and exponential backoff until
    ``timeout_s`` (default from ``ULG_WEB_TAB_LOCK_TIMEOUT_S``, else 120s).

    There is no cross-process FIFO; a later waiter may starve until the deadline.
    The kernel releases ``flock`` if the holder process dies.
    """
    tid = str(target_id or "").strip()
    if not tid:
        raise ValueError("target_id required for target_write_lock")
    limit = _default_timeout_s() if timeout_s is None else float(timeout_s)
    path = _lock_path(tid)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    async with _inproc(tid):
        fd = await _acquire_cross_process_lock(
            path, target_id=tid, deadline=deadline, op=op
        )
        try:
            yield
        finally:
            _release_cross_process_lock(fd)
