"""In-flight dispatch registry — concurrent-conflict detection with disk persistence.

A second ``op="dispatch"`` into a cwd that is already running rejects with
``reason_code="dispatch_conflict"``. The registry is also queried by
``op="worktree_remove"`` to detect ``worktree_busy`` (a dispatch is in
flight against a cwd inside the worktree being removed).

The registry is persisted to ``REGISTRY_PATH`` so ``worktree_busy`` rejection
stays reliable across MCP restarts. On startup, stale entries are pruned via a
PID check: all entries are owned by the writer process; if that PID is gone the
entries are stale. The recovery outcome is announced via
``mcp.grokbuild.registry.recovered``.

Reader/writer lock semantics (schema v3):
  edit      → grant iff no writer AND no readers
  read_only → grant iff no writer (readers coexist)

On conflict, dead subprocess holders and TTL-expired holders are reaped before
refusing (``mcp.grokbuild.lock.reaped``).

∀ write to ``_in_flight``: ``_write_registry_to_disk`` atomically persists the
new state (write-temp → ``os.replace``) so the file is never torn on a crash.
Schema versioned via ``SCHEMA_VERSION`` for clean future migrations.

∀ file writes inside async handlers: sync I/O is performed with the ``_lock``
held. The payload is always small (a list of cwds) so the blocking window is
negligible.

Sole-maintainer policy ([universal:no-bc]): schema_version mismatch on load
is treated as a stale file (entries pruned, registry rewritten). There is no
in-place migration — operators delete the registry on schema bumps.
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

from grokbuild.events import (
    emit_grok_build_lock_reaped,
    emit_grok_build_registry_recovered,
)
from grokbuild.lock_state import _Holder, _LockState

logger = get_logger(__name__)

_lock = asyncio.Lock()
# cwd(realpath) → lock state. Replaces the flat dispatch_id map.
_in_flight: dict[str, _LockState] = {}

SCHEMA_VERSION = 3
_STALE_TTL_SECONDS = int(os.getenv("GROKBUILD_LOCK_TTL_SECONDS", str(2 * 60 * 60)))
_DEFAULT_REGISTRY_PATH = "~/.local/share/grokbuild-worker/registry.json"


def _env_registry_path() -> Path:
    """Resolve registry path from env with expanduser (tilde → $HOME)."""
    raw = os.getenv("GROKBUILD_REGISTRY_PATH", _DEFAULT_REGISTRY_PATH)
    return Path(raw).expanduser()


REGISTRY_PATH = _env_registry_path()


def _canonical(cwd: str) -> str:
    return os.path.realpath(cwd)


def _pid_running(pid: int) -> bool:
    """Return True if process ``pid`` is currently running.

    Uses ``os.kill(pid, 0)`` — sends no signal, only checks existence.
    PermissionError means the PID exists but is owned by another user.
    """
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
    """Drop holders whose subprocess is dead or that exceeded the TTL.

    Caller MUST hold _lock. Conservative on the race window: a holder whose
    pid is still None and is within the TTL is treated as live (it may be
    between acquire and spawn). Beyond the TTL a None-pid holder is reaped —
    a dispatch that never recorded a pid in 2h is not coming back.
    """
    now = time.monotonic()
    reaped = 0

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
        reaped += 1
    for did in [d for d, h in state.readers.items() if _dead(h)]:
        logger.warning("reaping stale reader lock cwd=%s dispatch=%s", key, did)
        state.readers.pop(did, None)
        reaped += 1
    if reaped:
        emit_grok_build_lock_reaped(cwd=key, holders_reaped=reaped)


def _write_registry_to_disk() -> None:
    """Atomically overwrite the registry file with current ``_in_flight``.

    Write-temp-then-rename: the file is never in a torn state on crash.
    On ``OSError`` the failure is surfaced via ``logger.warning`` plus a
    ``mcp.grokbuild.registry.recovered`` event with ``entries_pruned=-1``
    sentinel — silent suppression would leave the in-memory registry
    inconsistent with disk, breaking the next-restart reap path.
    ∀ callers in async context: caller must hold ``_lock`` before calling.
    """
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
        fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".grokbuild_registry_")
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
            "grokbuild registry write failed: path=%s err=%s", REGISTRY_PATH, exc
        )
        emit_grok_build_registry_recovered(
            entries_recovered=0,
            entries_pruned=-1,  # sentinel: write-failure on persist path
            schema_version=SCHEMA_VERSION,
        )


def _load_registry_from_disk() -> None:
    """Load persisted registry on module init (server startup).

    Prunes all entries if the writer PID is gone (crash recovery). Emits
    ``mcp.grokbuild.registry.recovered`` so operators see what happened
    post-restart. Safe to call before the event loop starts:
    ``mcp_events.record`` uses a background thread queue with no
    event-loop dependency.
    """
    schema_version = SCHEMA_VERSION

    if not REGISTRY_PATH.exists():
        emit_grok_build_registry_recovered(
            entries_recovered=0,
            entries_pruned=0,
            schema_version=schema_version,
        )
        return

    try:
        data = json.loads(REGISTRY_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        emit_grok_build_registry_recovered(
            entries_recovered=0,
            entries_pruned=0,
            schema_version=schema_version,
        )
        return

    schema_version = data.get("schema_version", 0)
    writer_pid: int = data.get("writer_pid", 0)
    raw_entries: list[Any] = data.get("entries", [])

    # Sole-maintainer policy ([universal:no-bc]): schema_version mismatch is
    # treated as a stale file. The operator deletes the registry on schema
    # bumps; there is no in-place migration path.
    if schema_version != SCHEMA_VERSION:
        logger.warning(
            "grokbuild registry schema mismatch: file=%d expected=%d; discarding",
            schema_version,
            SCHEMA_VERSION,
        )
        emit_grok_build_registry_recovered(
            entries_recovered=0,
            entries_pruned=len(raw_entries),
            schema_version=schema_version,
        )
        return

    if not raw_entries:
        emit_grok_build_registry_recovered(
            entries_recovered=0,
            entries_pruned=0,
            schema_version=schema_version,
        )
        return

    # All entries are owned by writer_pid. If that process is gone, all are stale
    # (the dispatches it was managing are terminated). Overwrite with an empty
    # registry under the new MCP PID so the next persist is clean.
    if writer_pid and not _pid_running(writer_pid):
        entries_pruned = len(raw_entries)
        _write_registry_to_disk()
        emit_grok_build_registry_recovered(
            entries_recovered=0,
            entries_pruned=entries_pruned,
            schema_version=schema_version,
        )
    else:
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
        emit_grok_build_registry_recovered(
            entries_recovered=len(raw_entries),
            entries_pruned=0,
            schema_version=schema_version,
        )


async def try_acquire_cwd(cwd: str, dispatch_id: str = "", mode: str = "edit") -> bool:
    """Reserve the cwd under reader/writer rules. Return False on conflict.

    edit  → grant iff no writer AND no readers.
    read_only → grant iff no writer (readers coexist).
    On conflict, attempt stale-holder reap (Task 3) before refusing.
    """
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
        # read_only
        if state.writer is None:
            state.readers[dispatch_id] = holder
            _write_registry_to_disk()
            return True
        return False


async def release_cwd(cwd: str, dispatch_id: str = "") -> None:
    """Release this dispatch's hold. Idempotent. Removes the cwd when empty."""
    key = _canonical(cwd)
    async with _lock:
        state = _in_flight.get(key)
        if state is None:
            return
        if state.writer is not None and state.writer.dispatch_id == dispatch_id:
            state.writer = None
        else:
            state.readers.pop(dispatch_id, None)
        # Legacy/empty-dispatch_id callers (test fixtures) clear the writer.
        if not dispatch_id and state.writer is not None:
            state.writer = None
        if state.is_empty():
            _in_flight.pop(key, None)
        _write_registry_to_disk()


async def record_pid(cwd: str, dispatch_id: str, pid: int) -> None:
    """Attach the subprocess pid to a held lock so conflict-time reap can
    detect a dead holder. No-op if the dispatch no longer holds the cwd."""
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
    """Return dispatch_id holding ``cwd``, or None if not in flight.

    Empty-string registry values (legacy / test-injected) surface as None
    so callers can treat "unknown dispatch_id" uniformly.
    """
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


async def cwds_under(prefix: str) -> dict[str, str]:
    """Return {cwd: dispatch_id} for in-flight cwds equal to or inside ``prefix``.

    Used by ``worktree_remove`` (membership-only) and ``worktree_list``
    (needs dispatch_id). Comparison is path-based after canonicalization;
    the prefix is normalized to end with ``os.sep`` before substring match
    to avoid partial-name false positives (``/a/foo`` must not match
    prefix ``/a/foo-bar``).
    """
    canonical = _canonical(prefix)
    canonical_prefix = canonical if canonical.endswith(os.sep) else canonical + os.sep
    async with _lock:
        out: dict[str, str] = {}
        for c, state in _in_flight.items():
            if c == canonical or c.startswith(canonical_prefix):
                if state.writer is not None:
                    out[c] = state.writer.dispatch_id
                elif state.readers:
                    out[c] = next(iter(state.readers))
        return out


def _reset_for_tests() -> None:
    """Clear the in-flight registry. TEST-ONLY — not a public API."""
    _in_flight.clear()


# Load persisted registry at module import (server startup). Safe because
# mcp_events.record uses a background thread queue with no event-loop dependency.
_load_registry_from_disk()
