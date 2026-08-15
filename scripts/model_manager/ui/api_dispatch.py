"""Manage API dispatch helpers — JSON-RPC method routing to ServiceController.

Module-level helpers split from `api_server.py` so the connection / lifecycle
class can stay under SLOC ceiling and so dispatch logic remains testable in
isolation. Imported by `api_server._dispatch`.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from deploy_identity.code_version import process_age_s, resolve_code_version

from .controller.fleet_liveness import build_snapshot
from .controller.restart_drain import (
    BackgroundCompleteHook,
    BackgroundFailedHook,
    run_gated,
    run_gated_deferred,
    run_gated_drain_supervised,
    run_gated_drain_supervised_blocking,
)
from .controller.restart_intent_consumer import project_restart_intent_consumer
from .controller.restart_intent_store import (
    STATUS_CANCELLED,
    STATUS_PENDING_DRAIN,
    RestartIntentCancelError,
    RestartIntentStore,
)
from .controller.restart_window_ctl import lifecycle_with_restart_window
from .lifecycle_envelope import start_envelope

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from .controller.service_ctl.core import ServiceController
    from .model.service_state import ServiceInfo, ServiceState

# Service whitelists — kept local to dispatch to avoid cross-module mutation.
VALID_SERVICES = frozenset(
    {
        "gateway",
        "stargate",
        "rag",
        "cloud_proxy",
        "mcp",
        "event_service",
        "cortex_api",
        "agent_bus",
        "git_integration_worker",
        "email_bridge",
        "cdp_ask",
    }
)
REBUILD_SERVICES = frozenset(
    {
        "mcp",
        "event_service",
        "cortex_api",
        "agent_bus",
        "git_integration_worker",
        "email_bridge",
    }
)
SYNC_RESTART_SERVICES = frozenset(
    {
        "gateway",
        "mcp",
        "stargate",
        "rag",
        "cloud_proxy",
        "cortex_api",
        "agent_bus",
        "git_integration_worker",
        "event_service",
        "cdp_ask",
    }
)


async def execute(
    ctl: ServiceController,
    method: str,
    service: str,
    params: dict[str, Any],
    *,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Dispatch a validated JSON-RPC method to ServiceController."""
    svc = ctl.service_state

    match method:
        case "fleet_liveness":
            unexpected = sorted(set(params) - {"code_ref"})
            if unexpected:
                raise ValueError(
                    "fleet_liveness accepts only code_ref: "
                    + ", ".join(unexpected)
                )
            code_ref = params.get("code_ref")
            if code_ref is not None and not isinstance(code_ref, str):
                raise ValueError("fleet_liveness code_ref must be a string")
            return await asyncio.to_thread(
                build_snapshot, ctl.root, svc, code_ref=code_ref
            )

        case "status":
            infos = await asyncio.to_thread(svc.check_all)
            return {
                "services": {
                    i.name.lower().replace(" ", "_"): i.status.value for i in infos
                },
                "build": await asyncio.to_thread(_gateway_build_status, ctl),
            }

        case "health":
            require_service(service)
            info = await asyncio.to_thread(_check_one, svc, service)
            result = {
                "service": service,
                "status": info.status.value,
                "detail": info.detail,
            }
            if service == "gateway":
                result["build"] = await asyncio.to_thread(_gateway_build_status, ctl)
            return result

        case "wait_healthy":
            require_service(service)
            timeout = float(params.get("timeout", 120.0))
            waited = await _wait_healthy(svc, service, timeout)
            return {"healthy": True, "waited_s": round(waited, 1)}

        case "start":
            require_service(service)
            msg = await _start(ctl, service)
            return start_envelope(msg)

        case "stop":
            require_service(service)
            force = bool(params.get("force", False))
            if service == "git_integration_worker" and not force:
                return await _git_worker_drain_supervised(ctl, "stop")
            return await run_gated(
                ctl.restart_gate,
                "stop",
                service,
                force=force,
                lifecycle=lambda: _lifecycle_with_restart_window(
                    ctl,
                    service,
                    "stop",
                    lambda: _stop(ctl, service),
                ),
            )

        case "restart":
            require_service(service)
            if service == "mcp":
                # Plain stop→start reuses the baked /app image; alias to sync_restart
                # so services/mcp-server/ edits actually load (todo:mcp-restart-silent-noop-load).
                result = await _mcp_deferred_sync_restart(
                    ctl,
                    event_bus,
                    method="restart",
                    force=bool(params.get("force", False)),
                    no_cache=False,
                    scheduled_message=_MCP_RESTART_ALIASED_MSG,
                )
                return await _finalize_restart_rebuild(
                    ctl, service, result, restart_scheduled=True
                )
            force = bool(params.get("force", False))
            if service == "git_integration_worker" and not force:
                result = await _git_worker_drain_supervised(ctl, "restart")
            else:
                result = await run_gated(
                    ctl.restart_gate,
                    "restart",
                    service,
                    force=force,
                    lifecycle=lambda: _lifecycle_with_restart_window(
                        ctl,
                        service,
                        "restart",
                        lambda: _restart_cycle(ctl, service),
                    ),
                )
            return await _finalize_restart_rebuild(ctl, service, result)

        case "rebuild":
            require_service(service)
            if service not in REBUILD_SERVICES:
                raise ValueError(
                    f"rebuild not supported for '{service}'; "
                    f"supported: {', '.join(sorted(REBUILD_SERVICES))}"
                )
            if service == "mcp":
                result = await _mcp_deferred_sync_restart(
                    ctl,
                    event_bus,
                    method="rebuild",
                    gate_action="sync_restart",
                    force=bool(params.get("force", False)),
                    no_cache=True,
                )
                return await _finalize_restart_rebuild(
                    ctl, service, result, restart_scheduled=True
                )
            msg = await _rebuild(ctl, service)
            return await _finalize_restart_rebuild(
                ctl, service, {"status": "ok", "message": msg}
            )

        case "sync_restart":
            require_service(service)
            if service not in SYNC_RESTART_SERVICES:
                raise ValueError(
                    f"sync_restart not supported for '{service}'; "
                    f"supported: {', '.join(sorted(SYNC_RESTART_SERVICES))}"
                )
            if service == "mcp":
                return await _mcp_deferred_sync_restart(
                    ctl,
                    event_bus,
                    method="sync_restart",
                    force=bool(params.get("force", False)),
                    no_cache=False,
                )
            force = bool(params.get("force", False))
            if service == "git_integration_worker" and not force:
                return await _git_worker_drain_supervised(ctl, "sync_restart")
            return await run_gated(
                ctl.restart_gate,
                "sync_restart",
                service,
                force=force,
                lifecycle=lambda: _lifecycle_with_restart_window(
                    ctl,
                    service,
                    "sync_restart",
                    lambda: _sync_restart(ctl, service),
                ),
            )

        case "busy_status":
            return await _busy_status(ctl)

        case "restart_intent_status":
            return await _restart_intent_status(ctl, params)

        case "cancel_restart_intent":
            return await _cancel_restart_intent(ctl, params)

        case "whoami":
            return _whoami()

        case "charter_reload":
            # In-process charter-runner module reload + tick restart (no TUI quit).
            return await ctl.reload_charter_tick()

        case "charter_pause":
            timeout_raw = params.get("timeout", params.get("timeout_s", 1800.0))
            try:
                timeout_s = float(timeout_raw)
            except (TypeError, ValueError):
                timeout_s = 1800.0
            return await ctl.charter_pause(
                reason=str(params.get("reason") or ""),
                set_by=str(params.get("set_by") or "manage"),
                timeout_s=timeout_s,
            )

        case "charter_resume":
            return await ctl.charter_resume()

        case "charter_hold_status":
            return await ctl.charter_hold_status()

        case "charter_block_root":
            root_id = str(params.get("root_id") or "").strip()
            if not root_id:
                raise ValueError("charter_block_root requires 'root_id'")
            return await ctl.charter_block_root(
                root_id=root_id,
                reason=str(params.get("reason") or ""),
                set_by=str(params.get("set_by") or "manage"),
                unenroll=bool(params.get("unenroll", True)),
                clear_wip=bool(params.get("clear_wip", False)),
            )

        case "charter_unblock_root":
            root_id = str(params.get("root_id") or "").strip()
            if not root_id:
                raise ValueError("charter_unblock_root requires 'root_id'")
            return await ctl.charter_unblock_root(
                root_id=root_id,
                set_by=str(params.get("set_by") or "manage"),
                reenroll=bool(params.get("reenroll", False)),
            )

        case "charter_root_status":
            root_id = str(params.get("root_id") or "").strip()
            if not root_id:
                raise ValueError("charter_root_status requires 'root_id'")
            return await ctl.charter_root_status(root_id=root_id)

        case "fleet_sync_restart":
            return await _fleet(ctl, build=False, scope=str(params.get("scope", "all")))

        case "fleet_rebuild_deploy":
            return await _fleet(ctl, build=True, scope=str(params.get("scope", "all")))

        case _:
            raise ValueError(
                f"Unknown method: '{method}'. "
                "Valid: status, health, wait_healthy, start, stop, restart, "
                "sync_restart, rebuild, busy_status, restart_intent_status, "
                "cancel_restart_intent, "
                "whoami, charter_reload, charter_pause, charter_resume, "
                "charter_hold_status, charter_block_root, charter_unblock_root, "
                "charter_root_status, fleet_sync_restart, fleet_rebuild_deploy"
            )


def require_service(service: str) -> None:
    """Raise ValueError if service is not a known service name."""
    if service not in VALID_SERVICES:
        raise ValueError(
            f"Unknown service: '{service}'. Valid: {', '.join(sorted(VALID_SERVICES))}"
        )


_CHARTER_HARVEST_WAIT_HEALTHY_S = 120.0
_CHARTER_HARVEST_DEFER_MAX_ATTEMPTS = 40


async def _attach_harvest_authority_identity(
    ctl: ServiceController,
    service: str,
    result: dict[str, Any],
    before_snap: Any,
    *,
    readiness_proven: bool,
) -> dict[str, Any]:
    """Merge structured authority identity into a charter harvest restart outcome."""
    from .controller.service_ctl.authority_identity import finalize_authority_identity

    if before_snap is None:
        return result
    intent_id = result.get("restart_intent_id") or result.get("intent_id")
    authority_identity = await finalize_authority_identity(
        ctl.service_state,
        service,
        before_snap,
        readiness_proven=readiness_proven,
        intent_id=intent_id,
    )
    if authority_identity is None:
        return result
    return {**result, "authority_identity": authority_identity}


async def sync_restart_charter_harvest(
    ctl: ServiceController,
    service: str,
    *,
    event_bus: EventBus | None = None,
) -> dict[str, Any]:
    """Blocking sync_restart for charter harvest — awaits drain and health.

    Unlike MCP agent calls, the charter tick runs after the worker window closed,
    so git-integration-worker uses the blocking drain supervisor and other
    services poll until healthy or retry budget exhausts.
    """
    from .controller.service_ctl.authority_identity import snapshot_before_restart

    if service not in SYNC_RESTART_SERVICES:
        return {
            "status": "skipped",
            "service": service,
            "reason": "unsupported_service",
        }

    before_snap = await snapshot_before_restart(ctl.service_state, service)

    if service == "git_integration_worker":
        supervisor = ctl.build_git_worker_drain_supervisor(
            kill=ctl.git_worker_kill_for("sync_restart")
        )
        result = await run_gated_drain_supervised_blocking(
            ctl.restart_gate,
            "sync_restart",
            service,
            store=ctl.restart_intent_store,
            supervisor=supervisor,
            reason="charter harvest propagation",
        )
        proven = result.get("status") == "ok"
        return await _attach_harvest_authority_identity(
            ctl,
            service,
            result,
            before_snap,
            readiness_proven=proven,
        )

    if service == "mcp":
        result = await _mcp_deferred_sync_restart(
            ctl,
            event_bus,
            method="sync_restart",
            force=False,
            no_cache=False,
        )
        if result.get("status") == "deferred":
            return await _attach_harvest_authority_identity(
                ctl,
                service,
                {**result, "service": service, "outcome": "declined"},
                before_snap,
                readiness_proven=False,
            )
        if result.get("status") == "ok":
            try:
                waited = await _wait_healthy(
                    ctl.service_state, "mcp", _CHARTER_HARVEST_WAIT_HEALTHY_S
                )
            except TimeoutError as exc:
                return await _attach_harvest_authority_identity(
                    ctl,
                    service,
                    {
                        "status": "error",
                        "service": service,
                        "reason": str(exc),
                        "scheduled": result,
                        "outcome": "attempted_unproven",
                    },
                    before_snap,
                    readiness_proven=False,
                )
            return await _attach_harvest_authority_identity(
                ctl,
                service,
                {
                    **result,
                    "service": service,
                    "wait_healthy_s": waited,
                    "outcome": "proven",
                },
                before_snap,
                readiness_proven=True,
            )
        return await _attach_harvest_authority_identity(
            ctl,
            service,
            result,
            before_snap,
            readiness_proven=False,
        )

    last: dict[str, Any] = {"status": "error", "reason": "no_attempt"}
    for _ in range(_CHARTER_HARVEST_DEFER_MAX_ATTEMPTS):
        last = await run_gated(
            ctl.restart_gate,
            "sync_restart",
            service,
            force=False,
            lifecycle=lambda svc=service: _lifecycle_with_restart_window(
                ctl,
                svc,
                "sync_restart",
                lambda svc=service: _sync_restart(ctl, svc),
            ),
        )
        if last.get("status") != "deferred":
            break
        retry_s = max(1, int(last.get("retry_after_s") or 30))
        await asyncio.sleep(retry_s)

    if last.get("status") == "ok":
        try:
            waited = await _wait_healthy(
                ctl.service_state, service, _CHARTER_HARVEST_WAIT_HEALTHY_S
            )
            last = {**last, "wait_healthy_s": waited}
            return await _attach_harvest_authority_identity(
                ctl,
                service,
                last,
                before_snap,
                readiness_proven=True,
            )
        except TimeoutError as exc:
            return await _attach_harvest_authority_identity(
                ctl,
                service,
                {
                    "status": "error",
                    "service": service,
                    "reason": str(exc),
                    "restart": last,
                },
                before_snap,
                readiness_proven=False,
            )
    return await _attach_harvest_authority_identity(
        ctl,
        service,
        last,
        before_snap,
        readiness_proven=False,
    )


async def _git_worker_drain_supervised(
    ctl: ServiceController, action: str
) -> dict[str, Any]:
    """Route a non-force git-worker lifecycle action to the drain supervisor.

    git_integration_worker non-force stop/restart/sync_restart converge via the
    event-driven drain (durable intent + worker begin-drain + 202 deferred) rather
    than the generic busy-probe deferral. Busy work busy-skips into durable drain
    (todo:manage-busy-drain-restart); force=true keeps the existing immediate
    kill path. The terminal lifecycle is action-appropriate: stop vs restart.
    """
    supervisor = ctl.build_git_worker_drain_supervisor(
        kill=ctl.git_worker_kill_for(action)
    )
    return await run_gated_drain_supervised(
        ctl.restart_gate,
        action,
        "git_integration_worker",
        store=ctl.restart_intent_store,
        supervisor=supervisor,
        reason=f"manage {action} (deferred drain)",
    )


def _whoami() -> dict[str, Any]:
    """Read-only identity for the manage API process (no mutation)."""
    age_s = process_age_s()
    if age_s is not None:
        start_time = (datetime.now(UTC) - timedelta(seconds=age_s)).isoformat()
    else:
        start_time = "unknown"
    return {
        "pid": os.getpid(),
        "code_version": resolve_code_version(),
        "process_start_time": start_time,
    }


async def _busy_status(ctl: ServiceController) -> dict[str, Any]:
    """Per-service busy read model + process-level busy snapshot (no mutation).

    Reports, for every restart-eligible service, whether it is busy and whether a
    non-force restart would defer right now — computed from the same probe path
    ``run_gated`` uses, but WITHOUT acquiring any restart slot. The ``process``
    block surfaces the quit-guard accounting (``ManageShutdownGate``) so a UI can
    also reflect "the manage host itself is busy".
    """
    from .controller.busy_work_summary import format_active_work_summary

    report = await ctl.restart_gate.busy_report(sorted(SYNC_RESTART_SERVICES))
    now = datetime.now(UTC)
    store = ctl.restart_intent_store
    store.sweep_expired_windows(now=now)
    live_intents = {intent.service: intent for intent in store.pending_intents()}
    for service, entry in report.items():
        intent = live_intents.get(service)
        entry["restart_intent"] = (
            project_restart_intent_consumer(intent, now=now)
            if intent is not None
            else None
        )
        entry["restart_window"] = store.restart_window_for_service(service, now=now)
        entry["active_work_summary"] = format_active_work_summary(
            entry.get("active_work")
        )
    extra = ("build_image",) if ctl.build_running else ()
    snap = ctl.shutdown_gate.snapshot(extra_activities=extra)
    hold_status = await ctl.charter_hold_status()
    return {
        "services": report,
        "restart_windows": store.restart_window_projection(now=now),
        "process": {
            "manage_inflight": snap.manage_inflight,
            "activities": list(snap.activities),
        },
        "charter_hold": hold_status,
    }


async def _restart_intent_status(
    ctl: ServiceController, params: dict[str, Any]
) -> dict[str, Any]:
    """Read one restart intent by id or live intent for a service (no mutation)."""
    intent_id = str(params.get("intent_id") or "").strip()
    service = str(params.get("service") or "").strip()
    store = ctl.restart_intent_store
    now = datetime.now(UTC)
    intent = None
    if intent_id:
        intent = store.get(intent_id)
    elif service:
        intent = store.active_for_service(service)
    else:
        raise ValueError("restart_intent_status requires 'intent_id' or 'service'")
    if intent is None:
        return {"restart_intent": None}
    return {"restart_intent": project_restart_intent_consumer(intent, now=now)}


async def _cancel_restart_intent(
    ctl: ServiceController, params: dict[str, Any]
) -> dict[str, Any]:
    """Release-then-cancel a deferred restart intent (A′ retraction verb).

    When a drain epoch is set, worker ``POST .../cancel-drain`` runs first so a
    crash cannot leave ``cancelled`` without drain-release. Store cancel is
    refused after kill commit (``drained_restarting`` / other terminals).
    """
    intent_id = str(params.get("intent_id") or "").strip()
    if not intent_id:
        raise ValueError("cancel_restart_intent requires 'intent_id'")
    service_param = str(params.get("service") or "").strip() or None
    return await orchestrate_cancel_restart_intent(
        ctl.restart_intent_store,
        intent_id=intent_id,
        service=service_param,
        release_drain=_worker_cancel_drain,
    )


async def _worker_cancel_drain(intent_id: str, drain_epoch: int) -> dict[str, Any]:
    """POST worker admin cancel-drain for the matching intent generation."""
    from transport_utils import make_async_client

    from .controller.restart_drain import GIT_INTEGRATION_WORKER_URL

    async with make_async_client(GIT_INTEGRATION_WORKER_URL, timeout=10.0) as client:
        resp = await client.post(
            "/api/v1/git/admin/cancel-drain",
            json={"intent_id": intent_id, "drain_epoch": drain_epoch},
        )
        resp.raise_for_status()
        return resp.json()


async def orchestrate_cancel_restart_intent(
    store: RestartIntentStore,
    *,
    intent_id: str,
    service: str | None = None,
    release_drain: Callable[[str, int], Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Pure cancel orchestration — release (when epoch set) then store cancel.

    Extracted for unit tests that inject a fake ``release_drain`` without a live
    worker. Fail-closed: if release raises, the store is left uncancelled.
    """
    intent = store.get(intent_id)
    if intent is None:
        return {
            "status": "error",
            "reason": "intent_not_found",
            "intent_id": intent_id,
        }
    if service is not None and intent.service != service:
        return {
            "status": "error",
            "reason": "service_mismatch",
            "intent_id": intent_id,
            "service": intent.service,
            "requested_service": service,
        }
    if intent.status == STATUS_CANCELLED:
        return {
            "status": "cancelled",
            "intent_id": intent.intent_id,
            "service": intent.service,
            "already_cancelled": True,
            "drain_release": None,
        }
    if intent.status != STATUS_PENDING_DRAIN:
        return {
            "status": "refused",
            "reason": "cancel_refused_after_final_check_or_terminal",
            "intent_id": intent.intent_id,
            "intent_status": intent.status,
        }

    drain_release: dict[str, Any] | None = None
    if intent.drain_epoch is not None:
        if release_drain is None:
            return {
                "status": "error",
                "reason": "drain_release_unavailable",
                "intent_id": intent.intent_id,
                "drain_epoch": intent.drain_epoch,
            }
        try:
            drain_release = await release_drain(intent.intent_id, intent.drain_epoch)
        except Exception as exc:  # noqa: BLE001 — fail closed; do not cancel store
            return {
                "status": "error",
                "reason": "drain_release_failed",
                "intent_id": intent.intent_id,
                "drain_epoch": intent.drain_epoch,
                "error": str(exc),
            }

    try:
        cancelled = store.cancel(intent_id)
    except RestartIntentCancelError as exc:
        return {
            "status": "refused",
            "reason": exc.reason,
            "intent_id": intent_id,
            "intent_status": exc.status,
            "drain_release": drain_release,
        }
    return {
        "status": "cancelled",
        "intent_id": cancelled.intent_id,
        "service": cancelled.service,
        "intent_status": cancelled.status,
        "drain_epoch": cancelled.drain_epoch,
        "drain_release": drain_release,
        "already_cancelled": False,
    }


async def _fleet(ctl: ServiceController, *, build: bool, scope: str) -> dict[str, Any]:
    """Drive a headless fleet operation; agents observe progress via fleet.* events.

    Uses a no-op progress sink: the per-node log stream is a TUI affordance, while
    the coarse fleet.operation.*/fleet.service.* events already carry the structured
    outcome an agent needs. Phase 4 replaces the no-op sink with a streaming bridge.
    """
    from .controller.fleet import FleetOrchestrator, NullFleetSink

    orch = FleetOrchestrator(ctl=ctl, root=ctl.root, sink=NullFleetSink())
    result = await orch.sync_restart_all(build=build, scope=scope)
    return {
        "status": "ok" if result.success else "partial",
        "operation": result.operation,
        "build": result.build,
        "duration_s": round(result.duration_s, 1),
        "failures": result.failures,
    }


def write_json(writer: asyncio.StreamWriter, obj: dict[str, Any]) -> None:
    """Serialize obj to JSON and write a newline-terminated line to writer."""
    writer.write(json.dumps(obj).encode() + b"\n")


def _check_one(svc: ServiceState, service: str) -> ServiceInfo:
    """Return ServiceInfo for a single service (synchronous, call via to_thread)."""
    if service not in VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    try:
        return getattr(svc, f"check_{service}")()
    except AttributeError:
        raise ValueError(f"Unknown service: '{service}'")


async def _finalize_restart_rebuild(
    ctl: ServiceController,
    service: str,
    result: dict[str, Any],
    *,
    restart_scheduled: bool = False,
) -> dict[str, Any]:
    """Attach observed post-state; top-level ok implies observed liveness only."""
    from dataclasses import replace

    from .controller.service_ctl.post_state import (
        finalize_restart_rebuild_result,
        probe_service_post_state,
    )

    probe = await asyncio.to_thread(
        probe_service_post_state,
        ctl.service_state,
        service,
        check_one=_check_one,
    )
    if restart_scheduled:
        probe = replace(probe, restart_scheduled=True)
    return finalize_restart_rebuild_result(result, probe)


def _gateway_build_status(ctl: ServiceController) -> dict[str, Any]:
    """Return gateway build state for status/progress probes."""
    info = ctl.check_image()
    return {
        "running": ctl.build_running,
        "image_status": info.status.value,
        "image_id": info.image_id,
        "created": info.created,
        "size": info.size,
    }


async def _wait_healthy(svc: ServiceState, service: str, timeout: float) -> float:
    """Poll service health until RUNNING or timeout; return elapsed seconds."""
    from .model.service_state import ServiceStatus

    t0 = time.monotonic()
    deadline = t0 + timeout
    while True:
        info = await asyncio.to_thread(_check_one, svc, service)
        if info.status == ServiceStatus.RUNNING:
            return time.monotonic() - t0
        if info.status in (ServiceStatus.NOT_ENABLED, ServiceStatus.DISABLED):
            raise TimeoutError(
                f"'{service}' is {info.status.value}; wait_healthy not applicable"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"'{service}' not healthy after {timeout:.0f}s "
                f"(last status: {info.status.value})"
            )
        await asyncio.sleep(2.0)


async def _start(ctl: ServiceController, service: str) -> str:
    """Call the appropriate ServiceController start method."""
    if service not in VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    if service == "cdp_ask":
        return await ctl.start_cdp_ask()
    return await getattr(ctl, f"start_{service}")()


async def _lifecycle_with_restart_window(
    ctl: ServiceController,
    service: str,
    action: str,
    lifecycle: Callable[[], Awaitable[str]],
) -> str:
    return await lifecycle_with_restart_window(
        ctl.restart_intent_store, service, action, lifecycle
    )


async def _stop(ctl: ServiceController, service: str) -> str:
    """Call the appropriate ServiceController stop method."""
    if service not in VALID_SERVICES:
        raise ValueError(f"Unknown service: '{service}'")
    if service == "cdp_ask":
        return await ctl.stop_cdp_ask()
    return await getattr(ctl, f"stop_{service}")()


async def _restart_cycle(ctl: ServiceController, service: str) -> str:
    """Stop then start a service (the 'restart' action), returning a combined message.

    Packaged so `run_gated` holds the restart-mutex slot across the full
    stop→start cycle and releases it once both complete.
    """
    stop_msg = await _stop(ctl, service)
    start_msg = await _start(ctl, service)
    return f"{stop_msg}\n{start_msg}"


async def _rebuild(ctl: ServiceController, service: str) -> str:
    """Full --no-cache rebuild + restart of a managed service.

    Heavy path — pulls fresh base images and rebuilds every layer. For container
    services this can take tens of minutes (gateway: 60-90 min recompiling vLLM
    CUDA kernels). Reserved for engine/pip/Dockerfile changes; agents should
    prefer 'sync_restart' for routine code edits.

    `gateway` is intentionally absent here: agents reach gateway only via
    'sync_restart' (which is just 'restart' under bind-mount). Engine rebuilds
    go through the TUI Build Image flow.
    """
    if service == "event_service":
        return await ctl.rebuild_event_service()
    if service == "cortex_api":
        return await ctl.rebuild_cortex_api()
    if service == "agent_bus":
        return await ctl.rebuild_agent_bus()
    if service == "git_integration_worker":
        return await ctl.rebuild_git_integration_worker()
    if service == "email_bridge":
        return await ctl.rebuild_email_bridge(no_cache=True)
    raise ValueError(f"rebuild not supported for '{service}'")


# Services whose state or render logic can change the boot surface.
# After `sync_restart` on one of these, the boot-render-diff smoke gate runs
# against the last recorded baseline to surface drift at deploy time rather
# than on the next agent's session start. See agent-bus thread 883.
_BOOT_RENDER_DIFF_SERVICES = frozenset({"cortex_api", "mcp"})

_MCP_RESTART_SCHEDULED_MSG = (
    "MCP restart scheduled; container will drain and restart in background. "
    "Retry tool calls after ~30s if you see -32099 or transport errors. "
    "Follow with manage(action='wait_healthy', service='mcp') to confirm readiness."
)

_MCP_RESTART_ALIASED_MSG = (
    f"{_MCP_RESTART_SCHEDULED_MSG} "
    "Plain manage(action='restart', service='mcp') is aliased to sync_restart "
    "(docker cp syncs workspace source into /app before restart)."
)


def _mcp_background_hooks(
    event_bus: EventBus | None,
    method: str,
) -> tuple[BackgroundCompleteHook | None, BackgroundFailedHook | None]:
    """Optional manage.service.* events when a deferred MCP lifecycle finishes."""
    if event_bus is None:
        return None, None
    from .manage_events import ManageServiceCompleted, ManageServiceFailed

    async def on_complete(_message: str, duration_s: float) -> None:
        await event_bus.publish(
            ManageServiceCompleted(
                method=method,
                service="mcp",
                duration_s=round(duration_s, 3),
            )
        )

    async def on_failed(error: str, duration_s: float) -> None:
        await event_bus.publish(
            ManageServiceFailed(
                method=method,
                service="mcp",
                error=error,
                duration_s=round(duration_s, 3),
            )
        )

    return on_complete, on_failed


async def _mcp_deferred_sync_restart(
    ctl: ServiceController,
    event_bus: EventBus | None,
    *,
    method: str,
    force: bool,
    no_cache: bool,
    gate_action: str | None = None,
    scheduled_message: str | None = None,
) -> dict[str, Any]:
    """Deferred MCP sync+restart (shared by sync_restart, rebuild, and restart alias)."""
    on_ok, on_fail = _mcp_background_hooks(event_bus, method)
    return await run_gated_deferred(
        ctl.restart_gate,
        gate_action or method,
        "mcp",
        force=force,
        lifecycle=lambda: _mcp_deferred_lifecycle(ctl, no_cache=no_cache),
        scheduled_message=scheduled_message or _MCP_RESTART_SCHEDULED_MSG,
        on_background_complete=on_ok,
        on_background_failed=on_fail,
    )


async def _mcp_deferred_lifecycle(ctl: ServiceController, *, no_cache: bool) -> str:
    """Full MCP sync/restart + optional boot-render-diff (runs in background)."""
    return await _lifecycle_with_restart_window(
        ctl,
        "mcp",
        "sync_restart",
        lambda: _mcp_deferred_lifecycle_inner(ctl, no_cache=no_cache),
    )


async def _mcp_deferred_lifecycle_inner(
    ctl: ServiceController, *, no_cache: bool
) -> str:
    """Inner MCP lifecycle without the restart-window wrapper."""
    msg = await ctl.sync_restart_mcp(no_cache=no_cache)
    diff_msg = await _run_boot_render_diff()
    if diff_msg:
        msg = f"{msg}\n\n{diff_msg}"
    return msg


async def _sync_restart(ctl: ServiceController, service: str) -> str:
    """Deploy local source edits and bring the service back up.

    Per-service strategy:
      gateway      → restart (libs/, services/, config/ are bind-mounted)
      mcp          → deferred via ``run_gated_deferred`` in ``execute`` (API path)
      cdp_ask      → dedicated sync_restart (never coupled to mcp)
      stargate, rag, cloud_proxy, cortex_api, agent_bus, event_service → restart
    """
    if service == "cdp_ask":
        return await ctl.sync_restart_cdp_ask()
    stop_msg = await _stop(ctl, service)
    start_msg = await _start(ctl, service)
    msg = f"{stop_msg}\n{start_msg}"

    if service in _BOOT_RENDER_DIFF_SERVICES:
        diff_msg = await _run_boot_render_diff()
        if diff_msg:
            msg = f"{msg}\n\n{diff_msg}"

    return msg


async def _run_boot_render_diff() -> str:
    """Run `scripts/boot-render-diff` and return its advisory output.

    Fail-soft: never raises, never blocks sync_restart completion. A
    timeout, non-zero exit, or crashed subprocess becomes a brief warning
    line in the restart log — the restart itself is authoritative.
    """
    import pathlib
    import subprocess

    script = (
        pathlib.Path(__file__).resolve().parents[3] / "scripts" / "boot-render-diff"
    )
    if not script.is_file():
        return ""

    def _run() -> str:
        try:
            proc = subprocess.run(
                [str(script)],
                capture_output=True,
                text=True,
                timeout=45.0,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return (
                "[boot-render-diff] timed out after 45s (advisory; restart unaffected)"
            )
        except Exception as exc:  # pragma: no cover — defensive
            return f"[boot-render-diff] failed: {exc} (advisory; restart unaffected)"

        body = (proc.stdout or "").strip()
        if proc.returncode != 0 and proc.stderr:
            body = (body + "\n" + proc.stderr.strip()).strip()
        return body

    return await asyncio.to_thread(_run)
