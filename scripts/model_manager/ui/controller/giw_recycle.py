"""Manage-side ``recycle_giw`` — drain first, park at idle, force only if park refuses.

The life MCP sliver is a thin sock relay. Decision logic lives here: arm the
existing GIW drain supervisor in recycle mode (idle-on-no-progress, not a
wall-clock completion deadline) with park-first wired (steer-restart v1), so an
idle occupant is parked — bridge ``CancelRun``, row resumable after restart —
and the supervisor escalates to the kill callable only when the park is refused
for a reason a restart cannot clear.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from scripts.model_manager import observation_event as events

from ..model.service_state import ServiceStatus
from .drain_dead_recovery import force_start_and_validate
from .restart_drain import run_gated_drain_supervised

if TYPE_CHECKING:
    from .service_ctl.core import ServiceController

_SERVICE = "git_integration_worker"
_DEFAULT_IDLE_S = 180.0
_RECYCLE_DEADLINE_S = 604800.0  # 7d alert ceiling; idle gate is the escalate


def recycle_idle_s() -> float:
    """Return the occupant-idle window used before recycle escalates to force."""
    raw = os.environ.get("GIW_RECYCLE_IDLE_S")
    if not raw:
        return _DEFAULT_IDLE_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_IDLE_S
    return value if value > 0 else _DEFAULT_IDLE_S


def recycle_deadline_s() -> float:
    """Return the alert-only deadline for recycle-mode drain supervision."""
    return _RECYCLE_DEADLINE_S


def occupant_progress_fresh(
    drain_snap: dict[str, Any] | None,
    liveness_snap: dict[str, Any] | None,
    *,
    idle_s: float,
    previous_token: tuple[frozenset[str], tuple[tuple[str, str], ...], bool] | None,
) -> tuple[bool, tuple[frozenset[str], tuple[tuple[str, str], ...], bool]]:
    """True when occupancy, heartbeat fingerprint, or Auto idle age shows work."""
    snap = drain_snap or {}
    ops = snap.get("active_ops") or []
    op_ids = frozenset(str(op.get("op_id") or "") for op in ops if op.get("op_id"))
    heartbeats = tuple(
        sorted(
            (str(op.get("op_id") or ""), str(op.get("last_heartbeat_at") or ""))
            for op in ops
            if op.get("last_heartbeat_at")
        )
    )
    queue_health: dict[str, Any] = {}
    if isinstance(liveness_snap, dict):
        maybe = liveness_snap.get("queue_health")
        queue_health = maybe if isinstance(maybe, dict) else liveness_snap
    occupant_idle = queue_health.get("occupant_idle_s")
    auto_fresh = isinstance(occupant_idle, int | float) and occupant_idle < idle_s
    token = (op_ids, heartbeats, auto_fresh)
    if auto_fresh:
        return True, token
    if previous_token is None:
        return False, token
    prev_ids, prev_hb, _prev_fresh = previous_token
    if op_ids != prev_ids or heartbeats != prev_hb:
        return True, token
    return False, token


def refuse_foreign_service(service: str, params: dict[str, Any]) -> None:
    """Reject any service other than git_integration_worker, and any extra params."""
    requested = str(service or params.get("service") or "").strip()
    if requested and requested != _SERVICE:
        raise ValueError(
            "recycle_giw is hard-scoped to git_integration_worker; "
            f"refused service={requested!r}"
        )
    unexpected = sorted(set(params) - {"service"})
    if unexpected:
        raise ValueError("recycle_giw accepts no parameters: " + ", ".join(unexpected))


async def worker_is_stopped(ctl: ServiceController) -> bool:
    """True when the health checker reports the worker process is stopped.

    A missing checker is not stopped — callers keep the drain path. An
    exception from the checker is not stopped either; only an explicit
    ``stopped`` status skips the drain, because that is the case with nothing
    left to protect.
    """
    checker = getattr(
        getattr(ctl, "service_state", None), "check_git_integration_worker", None
    )
    if checker is None:
        return False
    info = await asyncio.to_thread(checker)
    return getattr(info, "status", None) is ServiceStatus.STOPPED


async def _start_stopped_worker(
    ctl: ServiceController, idle_s: float
) -> dict[str, Any]:
    """Start a stopped worker even when a drain intent is still pending."""
    store = ctl.restart_intent_store
    intent = store.active_for_service(_SERVICE)
    if intent is not None:
        message = await force_start_and_validate(
            store,
            intent,
            ctl.start_git_integration_worker,
            reason="recycle_stopped",
        )
        intent_id = intent.intent_id
    else:
        message = await ctl.start_git_integration_worker()
        intent_id = ""
    if intent_id:
        await events.emit_manage_recycle_drain_attempted(
            intent_id=intent_id, idle_s=idle_s
        )
    return {
        "status": "ok",
        "state": "started",
        "service": _SERVICE,
        "reason": "worker stopped; started regardless of drain state",
        "message": message,
        "restart_intent_id": intent_id or None,
        "recycle": True,
        "idle_s": idle_s,
        "idle_gate": "occupant_progress",
        "idle_action": "park_first",
    }


async def recycle_giw(
    ctl: ServiceController, params: dict[str, Any], service: str
) -> dict[str, Any]:
    """Arm drain-gated GIW recycle, or start the worker when it is already stopped.

    A stopped process has no occupants. Waiting on a pending drain intent would
    return "drain already in progress" and never start it.
    """
    refuse_foreign_service(service, params)
    idle_s = recycle_idle_s()
    await events.emit_manage_recycle_requested()
    if await worker_is_stopped(ctl):
        return await _start_stopped_worker(ctl, idle_s)
    supervisor = ctl.build_git_worker_drain_supervisor(
        kill=ctl.git_worker_kill_for("recycle_giw"),
        idle_escalate_s=idle_s,
        deadline_s=_RECYCLE_DEADLINE_S,
        park_first=True,
    )
    result = await run_gated_drain_supervised(
        ctl.restart_gate,
        "recycle_giw",
        _SERVICE,
        store=ctl.restart_intent_store,
        supervisor=supervisor,
        reason="manage recycle_giw (drain then idle-escalate)",
    )
    intent_id = str(result.get("restart_intent_id") or "")
    if intent_id:
        await events.emit_manage_recycle_drain_attempted(
            intent_id=intent_id, idle_s=idle_s
        )
    result = {
        **result,
        "recycle": True,
        "service": _SERVICE,
        "idle_s": idle_s,
        "idle_gate": "occupant_progress",
        "idle_action": "park_first",
    }
    return result


__all__ = [
    "occupant_progress_fresh",
    "recycle_deadline_s",
    "recycle_giw",
    "recycle_idle_s",
    "refuse_foreign_service",
]
