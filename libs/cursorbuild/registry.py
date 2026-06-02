"""In-flight dispatch registry — concurrent-conflict detection with disk persistence.

Forked from ``grokbuild.registry`` with a cursorbuild-specific default path so a
co-resident grokbuild worker never shares lock state (pre-flight two-writer note).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from cursorbuild.lock_state import _Holder, _LockState

logger = get_logger(__name__)

_lock = asyncio.Lock()
_in_flight: dict[str, _LockState] = {}

SCHEMA_VERSION = 1
_STALE_TTL_SECONDS = int(os.getenv("CURSORBUILD_LOCK_TTL_SECONDS", str(2 * 60 * 60)))
_DEFAULT_REGISTRY_PATH = "~/.local/share/cursorbuild-worker/registry.json"


def _env_registry_path() -> Path:
    raw = os.getenv("CURSORBUILD_REGISTRY_PATH", _DEFAULT_REGISTRY_PATH)
    return Path(raw).expanduser()


REGISTRY_PATH = _env_registry_path()


def _canonical(cwd: str) -> str:
    return os.path.realpath(cwd)


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _holder_to_dict(h: _Holder) -> dict[str, Any]:
    return {"dispatch_id": h.dispatch_id, "mode": h.mode, "pid": h.pid}


def _holder_from_dict(d: dict[str, Any]) -> _Holder:
    return _Holder(
        dispatch_id=d.get("dispatch_id", "") or "",
        mode=d.get("mode", "edit"),
        pid=d.get("pid"),
        acquired_at=time.monotonic(),
    )


def _reap_stale_holders(key: str, state: _LockState) -> None:
    now = time.monotonic()

    def _dead(h: _Holder) -> bool:
        if h.pid is not None and not _pid_running(h.pid):
            return True
        return (now - h.acquired_at) > _STALE_TTL_SECONDS

    if state.writer is not None and _dead(state.writer):
        logger.warning(
            "reaping stale writer lock cwd=%s dispatch=%s pid=%s",
            key,
            state.writer.dispatch_id,
            state.writer.pid,
        )
        state.writer = None
    for did in [d for d, h in state.readers.items() if _dead(h)]:
        logger.warning("reaping stale reader lock cwd=%s dispatch=%s", key, did)
        state.readers.pop(did, None)


def _write_registry_to_disk() -> None:
    entries: list[dict[str, Any]] = []
    for cwd in sorted(_in_flight):
        state = _in_flight[cwd]
        if state.is_empty():
            continue
        entry: dict[str, Any] = {"cwd": cwd}
        entry["writer"] = (
            _holder_to_dict(state.writer) if state.writer is not None else None
        )
        entry["readers"] = [_holder_to_dict(h) for h in state.readers.values()]
        entries.append(entry)
    data = {
        "schema_version": SCHEMA_VERSION,
        "writer_pid": os.getpid(),
        "entries": entries,
    }
    parent = REGISTRY_PATH.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".cursorbuild_registry_")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh)
            os.replace(tmp_path, REGISTRY_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.warning(
            "cursorbuild registry write failed: path=%s err=%s", REGISTRY_PATH, exc
        )


def _load_registry_from_disk() -> None:
    if not REGISTRY_PATH.exists():
        return
    try:
        data = json.loads(REGISTRY_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "cursorbuild registry load failed; discarding %s: %s", REGISTRY_PATH, exc
        )
        return
    if data.get("schema_version") != SCHEMA_VERSION:
        logger.warning(
            "cursorbuild registry schema mismatch; discarding %s", REGISTRY_PATH
        )
        return
    writer_pid: int = data.get("writer_pid", 0)
    raw_entries: list[Any] = data.get("entries", [])
    if writer_pid and not _pid_running(writer_pid):
        _write_registry_to_disk()
        return
    for entry in raw_entries:
        if not isinstance(entry, dict) or "cwd" not in entry:
            continue
        state = _LockState()
        writer_raw = entry.get("writer")
        if isinstance(writer_raw, dict):
            state.writer = _holder_from_dict(writer_raw)
        for reader_raw in entry.get("readers", []):
            if isinstance(reader_raw, dict):
                h = _holder_from_dict(reader_raw)
                state.readers[h.dispatch_id] = h
        if not state.is_empty():
            _in_flight[entry["cwd"]] = state


async def try_acquire_cwd(cwd: str, dispatch_id: str = "", mode: str = "edit") -> bool:
    key = _canonical(cwd)
    async with _lock:
        state = _in_flight.get(key)
        if state is not None:
            _reap_stale_holders(key, state)
            if state.is_empty():
                _in_flight.pop(key, None)
                state = None
        if state is None:
            state = _LockState()
            _in_flight[key] = state
        holder = _Holder(dispatch_id=dispatch_id, mode=mode)
        if mode == "edit":
            if state.writer is None and not state.readers:
                state.writer = holder
                _write_registry_to_disk()
                return True
            return False
        if state.writer is None:
            state.readers[dispatch_id] = holder
            _write_registry_to_disk()
            return True
        return False


async def release_cwd(cwd: str, dispatch_id: str = "") -> None:
    key = _canonical(cwd)
    async with _lock:
        state = _in_flight.get(key)
        if state is None:
            return
        if state.writer is not None and state.writer.dispatch_id == dispatch_id:
            state.writer = None
        else:
            state.readers.pop(dispatch_id, None)
        if not dispatch_id and state.writer is not None:
            state.writer = None
        if state.is_empty():
            _in_flight.pop(key, None)
        _write_registry_to_disk()


async def record_pid(cwd: str, dispatch_id: str, pid: int) -> None:
    key = _canonical(cwd)
    async with _lock:
        state = _in_flight.get(key)
        if state is None:
            return
        if state.writer is not None and state.writer.dispatch_id == dispatch_id:
            state.writer.pid = pid
        elif dispatch_id in state.readers:
            state.readers[dispatch_id].pid = pid
        _write_registry_to_disk()


async def get_dispatch_id(cwd: str) -> str | None:
    key = _canonical(cwd)
    async with _lock:
        state = _in_flight.get(key)
        if state is None:
            return None
        if state.writer is not None:
            return state.writer.dispatch_id or None
        for did in state.readers:
            return did or None
        return None


def _reset_for_tests() -> None:
    _in_flight.clear()


_load_registry_from_disk()
