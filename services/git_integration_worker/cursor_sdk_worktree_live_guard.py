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

import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from services.git_integration_worker.cursor_sdk_orphan import BridgeOccupancy

logger = get_logger(__name__)

_LIVE_LEDGER_STATUSES = ("admitted", "running", "queued", "parked_waiting")

# How long a bridge may keep claiming its worktree after its own dispatch went
# terminal. Not zero: a bridge flushes on the way out, and reaping mid-flush
# costs the write (the whole point of this module). Not unbounded either: a
# process that never exits then strands its branch forever, because the tree is
# never reaped and ``discharge_landed`` refuses with ``live_bridge`` on every
# retry (a:33686 — lane-11231 held ~50min past closeout at 0% CPU by pid
# 223045, fully landed and permanently undischargeable).
#
# 5min is ~10x the observed shutdown-flush window and ~0.1x the observed leak,
# so it separates the two without discriminating on a margin.
_TERMINAL_CLAIM_GRACE_S = 300.0

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
    from services.git_integration_worker import cursor_sdk_orphan

    global _occupancy_cache
    now = time.monotonic()
    cached = _occupancy_cache
    if cached is not None and now - cached[0] < _OCCUPANCY_TTL_S:
        return cached[1]
    bridges = cursor_sdk_orphan.live_bridge_occupancy()
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
    for index, part in enumerate(rel.parts):
        if part.startswith("lane-"):
            return str(root.joinpath(*rel.parts[: index + 1]))
    return str(root / rel.parts[0])


def _terminal_claim_expired(row: sqlite3.Row, *, now: datetime | None = None) -> bool:
    """Has this dispatch been terminal long enough that its claim is stale?

    False for anything still live, and false whenever the answer is unknown —
    an unparseable or missing ``terminal_at`` means we cannot prove the grace
    elapsed, and this module fails closed by construction.
    """
    if row["status"] in _LIVE_LEDGER_STATUSES:
        return False
    raw = row["terminal_at"]
    if not raw:
        return False
    try:
        terminal_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if terminal_at.tzinfo is None:
        terminal_at = terminal_at.replace(tzinfo=UTC)
    elapsed = ((now or datetime.now(UTC)) - terminal_at).total_seconds()
    return elapsed > _TERMINAL_CLAIM_GRACE_S


def _dispatch_worktree_paths(dispatch_id: str) -> set[str]:
    """Paths a dispatch id claims: its ledger lease key and its lane registry row.

    A live dispatch claims unconditionally. A dispatch that went terminal keeps
    its claim only for ``_TERMINAL_CLAIM_GRACE_S`` so an exiting bridge can
    finish flushing; past that the process is an orphan and the claim is stale,
    because otherwise the branch is stranded forever (a:33686).

    Releasing here is not the same as deleting: this only withdraws the
    *dispatch-derived* claim. ``worktree_held_by_live_bridge`` also matches a
    bridge's own ``cwd``, so a process genuinely standing in the tree keeps its
    protection regardless of what its ledger row says.
    """
    from services.git_integration_worker.cursor_sdk_worktree_registry import (
        lookup_dispatch_worktree,
    )

    paths: set[str] = set()
    try:
        with ledger_connection() as conn:
            row = conn.execute(
                "SELECT lease_key, source_repo, status, terminal_at "
                "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger must not unguard
        logger.warning(
            "live-bridge guard ledger lookup failed dispatch_id=%s err=%s",
            dispatch_id,
            exc,
        )
        row = None
    if row is not None and _terminal_claim_expired(row):
        # Known terminal AND past the flush grace — the only case safe to drop.
        # An absent row, an unreadable ledger, or a terminal_at we cannot parse
        # all fall through and still claim: "I could not tell" must never reap
        # a tree out from under running work.
        logger.warning(
            "orphan bridge: dispatch_id=%s went terminal (status=%s) at %s, "
            "past the %.0fs grace, but its process is still alive; releasing "
            "its stale worktree claim",
            dispatch_id,
            row["status"],
            row["terminal_at"],
            _TERMINAL_CLAIM_GRACE_S,
        )
        return paths
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
            path = containing_worktree_under_root(path=key, worktree_root=worktree_root)
            if path is not None:
                active.add(path)
    return active


def worktree_held_by_live_bridge(
    *,
    worktree_path: Path,
    worktree_root: Path | None = None,
    occupancy: list[BridgeOccupancy] | None = None,
    fresh: bool = False,
) -> int | None:
    """Pid of a live bridge standing in ``worktree_path``, or ``None`` if free.

    ``worktree_root`` defaults to the parent of ``worktree_path``, which makes
    the single-path check usable from ``prune_dispatch_worktree`` where the
    caller knows the tree but not the root.

    ``fresh=True`` drops the occupancy cache and rescans process truth.
    """
    if fresh:
        reset_occupancy_cache()
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
