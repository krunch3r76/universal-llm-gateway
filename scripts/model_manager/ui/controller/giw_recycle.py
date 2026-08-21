"""Manage-side ``recycle_giw`` — drain first, force only after occupant idle.

The life MCP sliver is a thin sock relay. Decision logic lives here: arm the
existing GIW drain supervisor in recycle mode (idle-on-no-progress, not a
wall-clock completion deadline), then let that supervisor escalate to the
same kill callable the force path uses.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from scripts.model_manager import observation_event as events

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
        raise ValueError(
            "recycle_giw accepts no parameters: " + ", ".join(unexpected)
        )


async def recycle_giw(ctl: ServiceController, params: dict[str, Any], service: str) -> dict[str, Any]:
    """Arm drain-gated GIW recycle and return the deferred 202 envelope."""
    refuse_foreign_service(service, params)
    idle_s = recycle_idle_s()
    await events.emit_manage_recycle_requested()
    supervisor = ctl.build_git_worker_drain_supervisor(
        kill=ctl.git_worker_kill_for("recycle_giw"),
        idle_escalate_s=idle_s,
        deadline_s=_RECYCLE_DEADLINE_S,
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
    }
    return result


__all__ = [
    "occupant_progress_fresh",
    "recycle_giw",
    "recycle_idle_s",
    "refuse_foreign_service",
]
