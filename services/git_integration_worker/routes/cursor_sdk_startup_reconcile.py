"""Boot-time cursor-sdk ledger reconcile after worker restart.

Isolates restart-survivor handling (orphan snapshot, bridge reap, terminal
marking, queued promotion) so slice edits do not touch the full dispatch route.
Monkeypatched names resolve through ``routes.cursor_sdk`` at call time.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from universal_logging import get_logger

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

logger = get_logger(__name__)


async def startup_ledger_reconcile(app: FastAPI) -> None:
    """Reconcile restart survivors: OS-reap bridges before lease release.

    For each ledger ``running`` orphan, reap via env∧bridge identity, emit
    honest ``bridge_aborted``, then release/restore and mark terminal; finally
    promote queued heads.
    """
    from services.git_integration_worker.routes import cursor_sdk as route_mod

    removed = await asyncio.to_thread(route_mod.prune_stale_dispatch_homes)
    if removed:
        logger.info("startup dispatch_home prune removed=%d", removed)
    cfg: WorkerConfig = app.state.worker_config
    wt_sweep = await asyncio.to_thread(
        route_mod.reap_orphan_worktrees,
        source_repo=cfg.source_repo,
        worktree_root=cfg.worktree_root,
    )
    if wt_sweep.reaped:
        logger.info(
            "startup orphan worktree reaper reaped=%d salvaged=%d branches_retained=%d",
            wt_sweep.reaped,
            wt_sweep.salvaged,
            wt_sweep.branches_retained,
        )
    from services.git_integration_worker.cursor_sdk_gate import (
        reclaim_cross_lane_phantom_holders,
    )

    reclaimed = await reclaim_cross_lane_phantom_holders()
    if reclaimed:
        logger.info("startup cross-lane phantom gate holders reclaimed=%s", reclaimed)
    ledger = CursorDispatchLedger.instance()
    controller = app.state.admission_controller
    # Snapshot before startup_reconcile mutates status — survivors it marks
    # failed would otherwise never reach running_orphans() and skip ES terminal.
    survivors = {orphan.dispatch_id: orphan for orphan in ledger.running_orphans()}
    repos = await asyncio.to_thread(
        ledger.startup_reconcile, worker_instance=controller.worker_id
    )
    for orphan in ledger.running_orphans():
        survivors.setdefault(orphan.dispatch_id, orphan)
    for orphan in survivors.values():
        reap = await asyncio.to_thread(
            route_mod.reap_orphan_bridge_os, orphan.dispatch_id
        )
        if reap.kill_failed:
            route_mod.emit_sdk_restart_bridge_reap_failed(
                dispatch_id=orphan.dispatch_id,
                thread_id=orphan.thread_id,
            )
        await route_mod.release_or_restore_for_child(dispatch_id=orphan.dispatch_id)
        survivor_prune = await asyncio.to_thread(
            route_mod.salvage_restart_survivor_worktree,
            dispatch_id=orphan.dispatch_id,
            source_repo=cfg.source_repo,
        )
        if survivor_prune.pruned and (
            survivor_prune.salvaged or survivor_prune.branch_retained
        ):
            logger.info(
                "startup survivor worktree salvaged dispatch_id=%s salvaged=%s "
                "branch_retained=%s",
                orphan.dispatch_id,
                survivor_prune.salvaged,
                survivor_prune.branch_retained,
            )
        lease_key = await asyncio.to_thread(
            ledger.mark_terminal,
            dispatch_id=orphan.dispatch_id,
            terminal_status="failed",
        )
        route_mod.emit_restart_survivor_terminal(
            orphan, bridge_aborted=reap.bridge_aborted
        )
        if lease_key:
            repos.append(lease_key)
    # Parked rows re-enter before queued heads: they held their write lease
    # when the restart began, so lineage continuity outranks FIFO newcomers.
    await route_mod._resume_parked_rows(
        controller=controller,
        cfg=cfg,
        code_version=str(getattr(app.state, "worker_version", "unknown")),
    )
    for lease_key in sorted(set(repos)):
        await route_mod._promote_queued_for_lease(
            lease_key=lease_key,
            controller=controller,
            request=None,
        )
