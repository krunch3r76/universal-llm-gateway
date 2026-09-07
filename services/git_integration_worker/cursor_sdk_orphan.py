"""Orphan tracking for cursor-sdk outer-timeout dispatches.

When the async outer watchdog fires, the sync worker thread is intentionally
non-cancellable. Without explicit orphan handling, heartbeats keep emitting
``frontier.sdk.worker.progress`` and an active tool leg can reset the httpx
read deadline indefinitely — observability and operators see FAILED on the bus
while the bridge keeps running (friction 23851).

``reap_orphan_bridge_os`` performs a best-effort out-of-process kill after GIW
restart by matching ``CURSOR_SDK_DISPATCH_ID`` in the process environment and
confirming cursor-sdk bridge identity via cmdline/exe (friction 26765).

``sweep_unowned_bridges`` covers what that reap cannot see: it keys off the
bridge processes themselves rather than off ledger rows, so a bridge whose row
already went terminal — or which never had a row, as with test fixtures — still
gets collected instead of surviving indefinitely (assertion 31706).

``live_bridge_occupancy`` inverts the same scan for the worktree reaper: the
question there is not "may this bridge be killed" but "which directories may
not be deleted while this bridge runs". Node's ``spawn`` reports a missing
``cwd`` as ``ENOENT`` naming the *shell* (``spawn /bin/bash ENOENT``), so a
reaped worktree under a live bridge presents as a phantom missing-bash failure
mid-dispatch rather than as a directory error (todo:cursor-sdk-bridge-death-root-cause H4).
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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

# Mirrors ``cursor_dispatch_ledger._STATUS_TERMINAL``; a row in any other state
# still owns its bridge.
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
# Grace before an unowned bridge is collectable. Wide enough to clear the
# pre-arm handshake window, where a bridge is live before its row is running.
_SWEEP_MIN_AGE_S = float(os.getenv("GIT_WORKER_BRIDGE_SWEEP_MIN_AGE_S", "1800"))


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


@dataclass(frozen=True, slots=True)
class BridgeOccupancy:
    """A live cursor-sdk bridge and the directory it is standing in.

    ``cwd`` is the bridge's working directory at scan time; ``dispatch_id`` is
    the ``CURSOR_SDK_DISPATCH_ID`` env stamp when the bridge was launched by a
    GIW dispatch overlay. Either one alone is enough to pin a worktree.
    """

    pid: int
    cwd: str | None
    dispatch_id: str | None


def live_bridge_occupancy() -> list[BridgeOccupancy]:
    """Enumerate live cursor-sdk bridges with their cwd and dispatch stamp.

    Read-only counterpart to ``reap_orphan_bridge_os``: same identity test, no
    kill. Best-effort and never raises — a process that vanishes or denies
    inspection mid-scan is skipped, so callers must treat the result as a lower
    bound on occupancy and fail closed on what it does report.
    """
    found: list[BridgeOccupancy] = []
    for proc in psutil.process_iter(["pid"]):
        try:
            if not is_cursor_sdk_bridge_process(proc):
                continue
            try:
                cwd = proc.cwd()
            except (psutil.AccessDenied, OSError):
                cwd = None
            try:
                dispatch_id = proc.environ().get(_ENV_DISPATCH_ID)
            except (psutil.AccessDenied, OSError):
                dispatch_id = None
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        found.append(BridgeOccupancy(pid=proc.pid, cwd=cwd, dispatch_id=dispatch_id))
    return found


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
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.TimeoutExpired,
        ) as exc:
            kill_failed = True
            logger.warning(
                "restart bridge reap kill failed: dispatch_id=%s pid=%s err=%s",
                dispatch_id,
                proc.pid,
                exc,
            )
    return BridgeReapResult(bridge_aborted=killed, kill_failed=kill_failed)


@dataclass(frozen=True, slots=True)
class BridgeSweepResult:
    """Outcome of one unowned-bridge sweep."""

    scanned: int = 0
    killed: list[int] = field(default_factory=list)
    kill_failed: list[int] = field(default_factory=list)


def _default_status_lookup(dispatch_id: str) -> dict[str, Any] | None:
    # Imported lazily: the ledger module is heavy and only this path needs it.
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    return CursorDispatchLedger.instance().dispatch_status_by_id(
        dispatch_id=dispatch_id
    )


def _bridge_is_owned(
    dispatch_id: str | None,
    status_lookup: Callable[[str], dict[str, Any] | None],
) -> bool:
    """Return whether some live dispatch still lays claim to this bridge."""
    if not dispatch_id:
        # No dispatch stamp at all: never launched by a GIW dispatch overlay
        # (test fixtures, manual runs). Nothing in this worker can own it.
        return False
    with _lock:
        if dispatch_id in _active_clients:
            return True
    try:
        row = status_lookup(dispatch_id)
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger must not kill
        logger.warning(
            "bridge sweep ledger lookup failed; treating as owned: "
            "dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        return True
    if row is None:
        return False
    return str(row.get("status") or "") not in _TERMINAL_STATUSES


def sweep_unowned_bridges(
    *,
    min_age_s: float = _SWEEP_MIN_AGE_S,
    status_lookup: Callable[[str], dict[str, Any] | None] | None = None,
) -> BridgeSweepResult:
    """Kill aged cursor-sdk bridges that no live dispatch owns.

    ``reap_orphan_bridge_os`` walks ledger rows, so it only ever reaches
    bridges whose row is still ``running``. Once a row goes terminal — or was
    never created — its bridge becomes unreachable by that path and survives
    every subsequent restart. This walks the processes instead.

    Best-effort and never raises: a sweep failure must not disturb dispatch.
    """
    lookup = status_lookup or _default_status_lookup
    now = time.time()
    scanned = 0
    killed: list[int] = []
    kill_failed: list[int] = []
    for proc in psutil.process_iter(["pid", "environ", "create_time"]):
        try:
            if not is_cursor_sdk_bridge_process(proc):
                continue
            scanned += 1
            age_s = now - proc.create_time()
            if age_s < min_age_s:
                continue
            dispatch_id = proc.environ().get(_ENV_DISPATCH_ID)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if _bridge_is_owned(dispatch_id, lookup):
            continue
        try:
            proc.kill()
            proc.wait(timeout=5.0)
            killed.append(proc.pid)
            logger.warning(
                "unowned cursor-sdk bridge swept: pid=%s dispatch_id=%s age_s=%.0f",
                proc.pid,
                dispatch_id or "<none>",
                age_s,
            )
        except (
            psutil.NoSuchProcess,
            psutil.AccessDenied,
            psutil.TimeoutExpired,
        ) as exc:
            kill_failed.append(proc.pid)
            logger.warning(
                "unowned cursor-sdk bridge sweep kill failed: pid=%s err=%s",
                proc.pid,
                exc,
            )
    return BridgeSweepResult(scanned=scanned, killed=killed, kill_failed=kill_failed)


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
