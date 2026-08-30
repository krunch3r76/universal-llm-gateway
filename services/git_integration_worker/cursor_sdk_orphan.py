"""Orphan tracking for cursor-sdk outer-timeout dispatches.

When the async outer watchdog fires, the sync worker thread is intentionally
non-cancellable. Without explicit orphan handling, heartbeats keep emitting
``frontier.sdk.worker.progress`` and an active tool leg can reset the httpx
read deadline indefinitely — observability and operators see FAILED on the bus
while the bridge keeps running (friction 23851).

``reap_orphan_bridge_os`` performs a best-effort out-of-process kill after GIW
restart by matching ``CURSOR_SDK_DISPATCH_ID`` in the process environment and
confirming cursor-sdk bridge identity via cmdline/exe (friction 26765).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil
from universal_logging import get_logger

if TYPE_CHECKING:
    from cursor_sdk import Client

logger = get_logger(__name__)

_ENV_DISPATCH_ID = "CURSOR_SDK_DISPATCH_ID"
_BRIDGE_BIN_ENV = "CURSOR_SDK_BRIDGE_BIN"
_BRIDGE_MARKERS = (
    "cursor-sdk-bridge",
    "cursor_sdk_bridge",
    "cursor-sdk bridge",
)

_lock = threading.Lock()
_active_clients: dict[str, Client] = {}
_orphaned: set[str] = set()


@dataclass(frozen=True, slots=True)
class BridgeReapResult:
    """Result of OS-level cursor-sdk bridge scavenger for one dispatch id.

    ``bridge_aborted`` is true when at least one matching bridge process was
    killed; ``kill_failed`` is true when a matched bridge resisted termination.
    """

    bridge_aborted: bool
    kill_failed: bool = False


def is_cursor_sdk_bridge_process(proc: psutil.Process) -> bool:
    """Return whether *proc* is a cursor-sdk bridge rather than an inherited env match.

    Requires bridge cmdline/exe markers. Env-only matches (dispatch id in environ,
    no bridge marker in cmdline/exe) return False.
    """
    try:
        cmdline = proc.cmdline()
        exe = proc.exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False
    haystack = f"{' '.join(cmdline)} {exe}".lower()
    configured = os.environ.get(_BRIDGE_BIN_ENV, "").strip()
    if configured and configured.lower() in haystack:
        return True
    return any(marker in haystack for marker in _BRIDGE_MARKERS)


def reap_orphan_bridge_os(dispatch_id: str) -> BridgeReapResult:
    """Kill a surviving cursor-sdk bridge subprocess stamped with *dispatch_id*.

    Scans live processes for ``CURSOR_SDK_DISPATCH_ID`` plus bridge cmdline/exe
    identity. Skips env-only matches. Returns a
    ``BridgeReapResult`` describing kill success and whether a matched bridge
    resisted termination; best-effort and never raises.
    """
    killed = False
    kill_failed = False
    for proc in psutil.process_iter(["pid", "environ"]):
        try:
            env = proc.environ()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if env.get(_ENV_DISPATCH_ID) != dispatch_id:
            continue
        if not is_cursor_sdk_bridge_process(proc):
            logger.warning(
                "restart bridge reap skipped non-bridge env match: "
                "dispatch_id=%s pid=%s",
                dispatch_id,
                proc.pid,
            )
            continue
        try:
            proc.kill()
            proc.wait(timeout=5.0)
            killed = True
            logger.warning(
                "restart bridge reaped via OS: dispatch_id=%s pid=%s",
                dispatch_id,
                proc.pid,
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            kill_failed = True
            logger.warning(
                "restart bridge reap kill failed: dispatch_id=%s pid=%s err=%s",
                dispatch_id,
                proc.pid,
                exc,
            )
    return BridgeReapResult(bridge_aborted=killed, kill_failed=kill_failed)


def shutdown_active_bridges() -> int:
    """Hard-close every in-process bridge client registered for this worker process.

    Invoked from the worker lifespan shutdown hook before drain; reuses
    ``abort_orphaned_bridge`` for each active dispatch id.
    """
    with _lock:
        items = list(_active_clients.items())
    aborted = 0
    for dispatch_id, client in items:
        if abort_orphaned_bridge(dispatch_id=dispatch_id, client=client):
            aborted += 1
    return aborted


def register_active_client(*, dispatch_id: str, client: Client) -> None:
    """Track a live SDK Client so outer-timeout/shutdown can abort its bridge."""
    with _lock:
        _active_clients[dispatch_id] = client


def unregister_active_client(*, dispatch_id: str) -> Client | None:
    """Drop registry entry for *dispatch_id*; return the Client if it was present."""
    with _lock:
        return _active_clients.pop(dispatch_id, None)


def is_dispatch_orphaned(*, dispatch_id: str) -> bool:
    """Return whether *dispatch_id* was marked orphaned after an outer timeout."""
    with _lock:
        return dispatch_id in _orphaned


def mark_dispatch_orphaned(*, dispatch_id: str) -> Client | None:
    """Flag dispatch orphaned and return the live bridge client, if any."""
    with _lock:
        _orphaned.add(dispatch_id)
        return _active_clients.get(dispatch_id)


def clear_dispatch_orphan_state(*, dispatch_id: str) -> None:
    """Clear orphan flag and active-client entry after a dispatch fully ends."""
    with _lock:
        _orphaned.discard(dispatch_id)
        _active_clients.pop(dispatch_id, None)


def abort_orphaned_bridge(*, dispatch_id: str, client: Client | None = None) -> bool:
    """Hard-close the bridge subprocess for a timed-out orphan dispatch."""
    with _lock:
        owned = _active_clients.pop(dispatch_id, None)
    target = owned or client
    if target is None:
        return False
    try:
        target.close()
    except Exception as exc:  # noqa: BLE001 — best-effort kill must not wedge timeout path
        logger.warning(
            "orphan bridge close failed: dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        return False
    logger.warning(
        "orphan bridge aborted after outer timeout: dispatch_id=%s",
        dispatch_id,
    )
    return True
