"""Event Service terminals for cursor-sdk dispatches orphaned by GIW restart."""

from __future__ import annotations

from services.git_integration_worker.cursor_dispatch_ledger import LedgerRow
from services.git_integration_worker.cursor_sdk_events import emit_sdk_worker_orphaned

_RESTART_SURVIVOR_TIMEOUT_S = 0.0


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
