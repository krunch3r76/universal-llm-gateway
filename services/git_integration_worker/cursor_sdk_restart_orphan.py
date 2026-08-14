"""Event Service terminals for cursor-sdk dispatches orphaned by GIW restart."""

from __future__ import annotations

from pathlib import Path

from services.git_integration_worker.cursor_dispatch_ledger import (
    CursorDispatchLedger,
    LedgerRow,
)
from services.git_integration_worker.cursor_sdk_events import emit_sdk_worker_orphaned
from services.git_integration_worker.cursor_sdk_lane_b_commit import (
    is_worktree_dirty,
    salvage_commit,
)
from services.git_integration_worker.cursor_sdk_worktree_prune import PruneResult
from services.git_integration_worker.cursor_sdk_worktree_registry import (
    lookup_dispatch_worktree,
)

_RESTART_SURVIVOR_TIMEOUT_S = 0.0


def load_ledger_row(
    ledger: CursorDispatchLedger, *, dispatch_id: str
) -> LedgerRow | None:
    """Load a minimal ``LedgerRow`` for stale-reclaim / survivor terminal emit."""
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT dispatch_id, thread_id, execution_id, caller_agent, "
            "resolved_model, state_root, sdk_agent_id, sdk_run_id, status, "
            "started_at, last_heartbeat_at "
            "FROM cursor_sdk_dispatches WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return LedgerRow(**{k: row[k] for k in row.keys()})


def salvage_restart_survivor_worktree(
    *,
    dispatch_id: str,
    source_repo: Path,
) -> PruneResult:
    """Salvage dirty lane trees onto the branch; the tree itself stays."""
    _ = source_repo
    record = lookup_dispatch_worktree(dispatch_id=dispatch_id)
    if record is None or not record.worktree_path.is_dir():
        return PruneResult(pruned=False)
    if not is_worktree_dirty(record.worktree_path):
        return PruneResult(pruned=False, branch_retained=True)
    salvage = salvage_commit(
        record.worktree_path,
        message=f"cursor-sdk: restart salvage {dispatch_id}",
    )
    return PruneResult(
        pruned=False,
        branch_retained=True,
        salvaged=salvage.committed,
        head_sha=salvage.head_sha,
        salvage_refused=salvage.refused,
    )


def emit_restart_survivor_terminal(
    orphan: LedgerRow,
    *,
    bridge_aborted: bool = False,
) -> None:
    """Publish a worker.orphaned terminal so SdkFold can close restart survivors."""
    execution_id = orphan.execution_id or orphan.dispatch_id
    emit_sdk_worker_orphaned(
        dispatch_id=orphan.dispatch_id,
        thread_id=orphan.thread_id,
        execution_id=execution_id,
        resolved_model=orphan.resolved_model,
        timeout_s=_RESTART_SURVIVOR_TIMEOUT_S,
        bridge_aborted=bridge_aborted,
    )
