"""Drain barriers for fleet stop — peers stay up until gated services clear.

Invariant (operator 2026-07-28): if any service prevents a fleet restart, ALL
services remain up until that drain completes. Never parallel-stop peers while a
supervised drain (e.g. git_integration_worker) is still waiting on in-flight work.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from universal_logging import get_logger

from .fleet_remote import _MASTER_ROW_KEY, _classify_result
from .restart_drain import run_gated_drain_supervised_blocking

if TYPE_CHECKING:
    from .fleet import FleetProgressSink
    from .service_ctl.core import ServiceController

logger = get_logger(__name__)

# Retained as a toggle (set True to re-exclude GIW from fleet stop/start).
# See todo:git-worker-drain-p3-fleet.
FLEET_SKIP_GIT_WORKER = False


async def drain_stop_git_worker(ctl: ServiceController) -> str:
    """Fleet git-worker STOP via supervised drain (action=stop).

    Awaits cooperative drain to terminal state (begin-drain →
    git_worker.drain.completed → SIGTERM). Busy in-flight work is NOT a stop
    failure: the blocking helper busy-skips the active-work probe and waits for
    ``active_count→0``.
    """
    from ..model.service_state import ServiceStatus

    info = await asyncio.to_thread(ctl.service_state.check_git_integration_worker)
    if info.status is ServiceStatus.STOPPED:
        # Drain gate probes /health and defers with probe_error when down —
        # that would fail-closed the fleet cycle even though nothing to drain.
        return "git-integration-worker is not running."

    supervisor = ctl.build_git_worker_drain_supervisor(
        kill=ctl.git_worker_kill_for("stop")
    )
    result = await run_gated_drain_supervised_blocking(
        ctl.restart_gate,
        "stop",
        "git_integration_worker",
        store=ctl.restart_intent_store,
        supervisor=supervisor,
        reason="fleet stop (supervised drain)",
    )
    intent_id = str(result.get("restart_intent_id", ""))[:8]
    drain_status = result.get("drain_status", result.get("status"))
    if result.get("status") == "ok":
        return (
            "git-integration-worker drained and stopped — worker is not running "
            f"(intent {intent_id})"
        )
    return (
        "git-integration-worker supervised drain did not converge: "
        f"{drain_status} (intent {intent_id})"
    )


async def drain_local_barriers_before_stop(
    ctl: ServiceController, sink: FleetProgressSink
) -> list[tuple[str, bool, str, float]]:
    """Stop drain-gated local services while peers/remotes remain up.

    Call this BEFORE any peer or remote stop. On failure, callers must abort the
    fleet stop phase (fail closed) so the rest of the fleet stays running.
    """
    if FLEET_SKIP_GIT_WORKER:
        return []

    mk = _MASTER_ROW_KEY
    sink.line(
        mk,
        "Draining git_integration_worker before peer stop "
        "(peers and remotes stay up until drain completes)...",
    )
    t0 = asyncio.get_running_loop().time()
    try:
        msg = await drain_stop_git_worker(ctl)
        ok = _classify_result(msg)
    except Exception as exc:
        logger.exception("drain barrier git_integration_worker raised")
        msg = str(exc)
        ok = False
    elapsed = asyncio.get_running_loop().time() - t0
    sink.line(
        mk,
        f"  {'✓' if ok else '⚠'} drain git_integration_worker ({elapsed:.1f}s)",
    )
    if not ok:
        logger.warning("drain barrier git_integration_worker: %s", msg)
        sink.line(
            mk,
            "Drain barrier failed — aborting peer/remote stop (fleet remains up).",
        )
    return [("git_integration_worker", ok, msg, elapsed)]
