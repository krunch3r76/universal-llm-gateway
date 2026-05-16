"""In-flight dispatch registry — concurrent-conflict detection with disk persistence.

A second ``op="dispatch"`` into a cwd that is already running rejects with
``reason_code="dispatch_conflict"``. The registry is also queried by
``op="worktree_remove"`` to detect ``worktree_busy`` (a dispatch is in
flight against a cwd inside the worktree being removed).

The registry is persisted to ``REGISTRY_PATH`` so ``worktree_busy`` rejection
stays reliable across MCP restarts. On startup, stale entries are pruned via a
PID check: all entries are owned by the writer process; if that PID is gone the
entries are stale. The recovery outcome is announced via
``mcp.grok.build.registry.recovered``.

∀ write to ``_in_flight``: ``_write_registry_to_disk`` atomically persists the
new state (write-temp → ``os.replace``) so the file is never torn on a crash.
Schema versioned via ``SCHEMA_VERSION`` for clean future migrations.

∀ file writes inside async handlers: sync I/O is performed with the ``_lock``
held. The payload is always small (a list of cwds) so the blocking window is
negligible.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

from tools._grok_build_events import emit_grok_build_registry_recovered

_lock = asyncio.Lock()
_in_flight: set[str] = set()

SCHEMA_VERSION = 1
REGISTRY_PATH = Path(
    os.getenv("GROK_BUILD_REGISTRY_PATH", "/data/state/grok_build_registry.json")
)


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


def _write_registry_to_disk() -> None:
    """Atomically overwrite the registry file with current ``_in_flight``.

    Write-temp-then-rename: the file is never in a torn state on crash.
    Silently skips on ``OSError`` (volume not mounted, permissions, etc.).
    ∀ callers in async context: caller must hold ``_lock`` before calling.
    """
    data = {
        "schema_version": SCHEMA_VERSION,
        "writer_pid": os.getpid(),
        "entries": sorted(_in_flight),
    }
    parent = REGISTRY_PATH.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".grok_build_registry_")
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
    except OSError:
        pass


def _load_registry_from_disk() -> None:
    """Load persisted registry on module init (server startup).

    Prunes all entries if the writer PID is gone (crash recovery). Emits
    ``mcp.grok.build.registry.recovered`` so operators see what happened
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
    raw_entries: list[str] = data.get("entries", [])

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
            _in_flight.add(entry)
        emit_grok_build_registry_recovered(
            entries_recovered=len(raw_entries),
            entries_pruned=0,
            schema_version=schema_version,
        )


async def try_acquire_cwd(cwd: str) -> bool:
    """Reserve the cwd; return False if already in flight.

    Caller MUST pair every True return with a ``release_cwd`` call
    (typically in a ``finally`` block).
    """
    key = _canonical(cwd)
    async with _lock:
        if key in _in_flight:
            return False
        _in_flight.add(key)
        _write_registry_to_disk()
        return True


async def release_cwd(cwd: str) -> None:
    """Release the cwd. Idempotent — silent if absent."""
    key = _canonical(cwd)
    async with _lock:
        _in_flight.discard(key)
        _write_registry_to_disk()


async def cwds_under(prefix: str) -> set[str]:
    """Return in-flight cwds that are equal to or inside ``prefix``.

    Used by ``worktree_remove`` to detect ``worktree_busy``. Comparison
    is path-based after canonicalization; the prefix is normalized to
    end with ``os.sep`` before substring match to avoid partial-name
    false positives (``/a/foo`` must not match prefix ``/a/foo-bar``).
    """
    canonical = _canonical(prefix)
    canonical_prefix = canonical if canonical.endswith(os.sep) else canonical + os.sep
    async with _lock:
        return {
            c for c in _in_flight if c == canonical or c.startswith(canonical_prefix)
        }


def _reset_for_tests() -> None:
    """Clear the in-flight set. TEST-ONLY — not a public API."""
    _in_flight.clear()


# Load persisted registry at module import (server startup). Safe because
# mcp_events.record uses a background thread queue with no event-loop dependency.
_load_registry_from_disk()
