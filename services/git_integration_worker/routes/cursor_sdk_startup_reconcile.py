"""Boot-time cursor-sdk ledger reconcile after worker restart.

Isolates restart-survivor handling (orphan snapshot, bridge reap, boot rewire,
queued promotion) so slice edits do not touch the full dispatch route.
Monkeypatched names resolve through ``routes.cursor_sdk`` at call time.
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from universal_logging import get_logger

from services.git_integration_worker.config import WorkerConfig
from services.git_integration_worker.cursor_dispatch_ledger import CursorDispatchLedger

logger = get_logger(__name__)


def _lease_key_for(ledger: CursorDispatchLedger, dispatch_id: str) -> str | None:
    with ledger._connect() as conn:
        row = conn.execute(
            "SELECT lease_key, source_repo FROM cursor_sdk_dispatches "
            "WHERE dispatch_id=?",
            (dispatch_id,),
        ).fetchone()
    if row is None:
        return None
    return row["lease_key"] or row["source_repo"]


async def startup_ledger_reconcile(app: FastAPI) -> None:
    """Reconcile restart survivors: OS-reap bridges, then rewire unparked running.

    ``running`` rows with no live local task are restart orphans (``_tasks`` is
    empty after process start). Default matches cdp-ask boot rehydrate: stamp a
    park and re-admit a ``resume_of`` child that inherits ``execution_id`` /
    ``thread_id`` — do not require a prior park, do not mark-failed. Terminal
    rows are never in ``running_orphans`` and are left alone.
    """
    from services.git_integration_worker.cursor_sdk_await_reply import (
        seal_running_await_on_boot,
    )
    from services.git_integration_worker.cursor_sdk_park_ledger import mark_parked
    from services.git_integration_worker.cursor_sdk_park_resume import (
        BOOT_REWIRE_REASON,
    )
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
    # Park-rewire before startup_reconcile so unparked running are cancelled
    # (not failed) when the orphan scan runs; resume_parked_dispatches then
    # admits the resume_of child (BOOT_REWIRE_REASON skips same-process refuse).
    survivors = {orphan.dispatch_id: orphan for orphan in ledger.running_orphans()}
    repos: list[str] = []
    started_at = controller.worker_started_at or ""
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
        if await asyncio.to_thread(seal_running_await_on_boot, orphan.dispatch_id):
            # Keep park_kind=await_cdp_reply. mark_parked would rewrite it as
            # park_for_restart and resume before the CDP reply lands.
            logger.info(
                "startup preserved await_cdp_reply dispatch_id=%s thread_id=%s",
                orphan.dispatch_id,
                orphan.thread_id,
            )
            continue
        parked = await asyncio.to_thread(
            mark_parked,
            dispatch_id=orphan.dispatch_id,
            intent_id=None,
            drain_epoch=None,
            actor="giw_startup",
            reason=BOOT_REWIRE_REASON,
            requested_at=started_at or orphan.started_at or "",
            method="boot_rewire",
            tool_call_count=0,
            last_tool_calls=[],
            sidecar_uri=None,
        )
        if parked is None:
            # Row vanished mid-boot; last-resort fail so the lease frees.
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
            continue
        lease_key = await asyncio.to_thread(_lease_key_for, ledger, orphan.dispatch_id)
        if lease_key:
            repos.append(lease_key)
        logger.info(
            "startup boot-rewire parked dispatch_id=%s thread_id=%s execution_id=%s",
            orphan.dispatch_id,
            orphan.thread_id,
            orphan.execution_id or orphan.dispatch_id,
        )
    repos.extend(
        await asyncio.to_thread(
            ledger.startup_reconcile, worker_instance=controller.worker_id
        )
    )
    # Parked rows (prior park_for_restart + boot-rewire) re-enter before queued
    # heads: lineage continuity outranks FIFO newcomers.
    code_version = str(getattr(app.state, "worker_version", "unknown"))
    await route_mod._resume_parked_rows(
        controller=controller,
        cfg=cfg,
        code_version=code_version,
    )
    # Await parks are not running orphans. Re-arm them at boot; the sweeper's
    # first pass sleeps CURSOR_STALE_SWEEP_S before it would otherwise notice
    # a reply that landed while this process was down.
    await route_mod._resume_await_reply_rows(
        controller=controller,
        cfg=cfg,
        code_version=code_version,
    )
    for lease_key in sorted(set(repos)):
        await route_mod._promote_queued_for_lease(
            lease_key=lease_key,
            controller=controller,
            request=None,
        )
