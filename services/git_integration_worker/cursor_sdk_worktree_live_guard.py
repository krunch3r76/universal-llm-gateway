"""Live-occupancy guard for Lane-B worktree removal (H4).

Every remove path in this service decided reapability from *records* — ledger
status, registry rows, ``git worktree list``. None of them asked the one
question that matters to a running agent: is a process standing in that
directory right now? When registry, ledger, and git disagree (a status that
lags, a row that was unregistered early, a lease key that points at the hub
rather than the lane), the sweeper could delete a worktree whose bridge was
mid-dispatch.

The failure does not look like a deleted directory. Node's ``spawn`` with a
missing ``cwd`` raises ``ENOENT`` naming the *executable* it was about to run,
so the agent's next shell tool call dies with ``spawn /bin/bash ENOENT`` — a
message that points at the host's shell, which is present and fine. Six of six
ledger-correlated bridge-stderr samples had a ``lease_key`` under
``ulg-arc-worktrees/lane-{thread}`` that was absent from disk at failure time
(closeout ``82efb6a3abe7-bbc1543d``).

So the guard is process truth, not record truth, and it fails closed: a path we
cannot prove unoccupied is skipped for this sweep. Skipping costs one 30s cycle;
deleting costs a live dispatch.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from services.git_integration_worker.cursor_sdk_orphan import BridgeOccupancy

logger = get_logger(__name__)

_LIVE_LEDGER_STATUSES = ("admitted", "running", "queued", "parked_waiting")

# One sweep asks the guard from four places (active set, reap loop, reconcile,
# and once per prune candidate). A full ``process_iter`` with cmdline+exe reads
# for each of those is wasted work on a 30s cadence, so the roster is held
# briefly. The window only ever over-protects: a bridge that exited within it
# is still treated as present, and a bridge younger than it belongs to a
# dispatch whose lane row the ledger-derived active set already covers.
_OCCUPANCY_TTL_S = 3.0
_occupancy_cache: tuple[float, list[BridgeOccupancy]] | None = None


def reset_occupancy_cache() -> None:
    """Drop the cached bridge roster (tests, and after a deliberate kill)."""
    global _occupancy_cache
    _occupancy_cache = None


def _occupancy_snapshot() -> list[BridgeOccupancy]:
    from services.git_integration_worker.cursor_sdk_orphan import (
        live_bridge_occupancy,
    )

    global _occupancy_cache
    now = time.monotonic()
    cached = _occupancy_cache
    if cached is not None and now - cached[0] < _OCCUPANCY_TTL_S:
        return cached[1]
    bridges = live_bridge_occupancy()
    _occupancy_cache = (now, bridges)
    return bridges


def ledger_connection():  # noqa: ANN201 — sqlite3.Connection context manager
    """Connect to the ledger through the path the ledger pinned at construction.

    ``_ledger_path()`` re-resolves ``DATA_DIR``/``$HOME`` on every call, and this
    worker swaps ``HOME`` for the duration of a dispatch. A guard that resolved
    the path mid-swap would open an empty ``<dispatch-home>/.gateway`` DB, find
    no live rows, and conclude every lane tree was free — the blindness this
    module exists to prevent, arriving through the back door.
    """
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    return CursorDispatchLedger.instance()._connect()


def containing_worktree_under_root(
    *,
    path: Path | str,
    worktree_root: Path,
) -> str | None:
    """Resolve *path* to the worktree directory beneath ``worktree_root``.

    A bridge's cwd is often a subdirectory of its lane tree, so the raw cwd
    rarely equals the path the reaper is about to remove. Returns the resolved
    top-level child of the root that contains *path*, or ``None`` when *path*
    is outside the root (or is the root itself, which is never a worktree).
    """
    root = worktree_root.resolve()
    try:
        candidate = Path(path).resolve()
    except (OSError, RuntimeError):
        return None
    if candidate == root:
        return None
    try:
        rel = candidate.relative_to(root)
    except ValueError:
        return None
    if not rel.parts:
        return None
    return str(root / rel.parts[0])


def _dispatch_worktree_paths(dispatch_id: str) -> set[str]:
    """Paths a dispatch id claims: its ledger lease key and its lane registry row."""
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        lookup_dispatch_worktree,
    )

    paths: set[str] = set()
    try:
        with ledger_connection() as conn:
            row = conn.execute(
                "SELECT lease_key, source_repo FROM cursor_sdk_dispatches "
                "WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger must not unguard
        logger.warning(
            "live-bridge guard ledger lookup failed dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        row = None
    if row is not None:
        for key in (row["lease_key"], row["source_repo"]):
            if key:
                paths.add(str(Path(key).resolve()))
    try:
        record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    except Exception as exc:  # noqa: BLE001 — same fail-open-on-read, fail-closed-on-remove
        logger.warning(
            "live-bridge guard registry lookup failed dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        record = None
    if record is not None:
        paths.add(str(record.worktree_path.resolve()))
    return paths


def live_bridge_worktree_paths(
    *,
    worktree_root: Path,
    occupancy: list[BridgeOccupancy] | None = None,
) -> set[str]:
    """Worktree paths under ``worktree_root`` held by a live cursor-sdk bridge.

    Two independent signals, unioned: the bridge's own cwd, and the worktree
    that its ``CURSOR_SDK_DISPATCH_ID`` resolves to through ledger lease key or
    lane registry row. The env stamp catches a bridge that has chdir'd away
    from its lane; the cwd catches a bridge whose ledger row has already gone
    terminal or was never written.
    """
    bridges = occupancy if occupancy is not None else _occupancy_snapshot()
    held: set[str] = set()
    for bridge in bridges:
        if bridge.cwd:
            path = containing_worktree_under_root(
                path=bridge.cwd, worktree_root=worktree_root
            )
            if path is not None:
                held.add(path)
        if not bridge.dispatch_id:
            continue
        for claimed in _dispatch_worktree_paths(bridge.dispatch_id):
            path = containing_worktree_under_root(
                path=claimed, worktree_root=worktree_root
            )
            if path is not None:
                held.add(path)
    return held


def live_ledger_worktree_paths(*, worktree_root: Path) -> set[str]:
    """Worktree paths claimed by non-terminal ledger rows, via lease key or lane row.

    The lease-key scan alone misses a live dispatch whose lane registry row
    holds the real path while its lease key points elsewhere, and it misses
    rows whose registry status lags behind the ledger. Joining the lane
    registry on ``thread_id`` and ``last_dispatch_id`` closes both.
    """
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        ensure_worktree_schema,
    )

    root = str(worktree_root.resolve())
    active: set[str] = set()
    placeholders = ", ".join("?" for _ in _LIVE_LEDGER_STATUSES)
    with ledger_connection() as conn:
        ensure_worktree_schema(conn)
        rows = conn.execute(
            "SELECT d.lease_key, d.source_repo, w.worktree_path "
            "FROM cursor_sdk_dispatches d "
            "LEFT JOIN cursor_sdk_lane_worktrees w "
            "  ON w.thread_id = d.thread_id OR w.last_dispatch_id = d.dispatch_id "
            f"WHERE d.status IN ({placeholders})",
            _LIVE_LEDGER_STATUSES,
        ).fetchall()
    for row in rows:
        for key in (row["lease_key"] or row["source_repo"], row["worktree_path"]):
            if not key:
                continue
            resolved = str(Path(key).resolve())
            if resolved.startswith(root):
                active.add(resolved)
    return active


def worktree_held_by_live_bridge(
    *,
    worktree_path: Path,
    worktree_root: Path | None = None,
    occupancy: list[BridgeOccupancy] | None = None,
) -> int | None:
    """Pid of a live bridge standing in ``worktree_path``, or ``None`` if free.

    ``worktree_root`` defaults to the parent of ``worktree_path``, which makes
    the single-path check usable from ``prune_dispatch_worktree`` where the
    caller knows the tree but not the root.
    """
    target = worktree_path.resolve()
    root = (worktree_root or worktree_path.parent).resolve()
    bridges = occupancy if occupancy is not None else _occupancy_snapshot()
    for bridge in bridges:
        claims = set()
        if bridge.cwd:
            claims.add(bridge.cwd)
        if bridge.dispatch_id:
            claims |= _dispatch_worktree_paths(bridge.dispatch_id)
        for claim in claims:
            resolved = containing_worktree_under_root(path=claim, worktree_root=root)
            if resolved == str(target):
                return bridge.pid
            try:
                if Path(claim).resolve() == target:
                    return bridge.pid
            except (OSError, RuntimeError):
                continue
    return None
