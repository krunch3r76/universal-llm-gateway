"""Drain-aware restart gate for manage-initiated service lifecycle ops.

Before a manage stop/restart/sync_restart kills a service, the gate consults a
per-service busy probe. When the target reports in-flight work and the caller did
not force, the restart is *deferred* with a structured, retryable outcome that
mirrors the MCP server's own restart-drain contract
(``services/mcp-server/middleware/drain.py``): a ``reason`` string and a
``retry_after_s`` hint the agent already knows how to honor.

Coalescing: each service has a ``FifoCapacityGate(limit=1)`` restart mutex
(``libs/universal_concurrency``) so two concurrent agents — or an agent and the
TUI operator — cannot drive overlapping stop/start cycles. A second caller while a
restart is in flight gets ``state="in_progress"``.

Authority lives here (the manage process) rather than in the MCP ``manage`` tool,
because both MCP agents and the TUI reach lifecycle through the shared
ServiceController. The single shared entry point is ``run_gated`` — called by both
the MCP dispatch path (``api_dispatch.execute``) and the TUI workers
(``view/screens/services.py``) — so a guard cannot be bypassed by either path.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import httpx
from transport_utils import make_async_client
from universal_concurrency import FifoCapacityGate
from universal_logging import get_logger

from .restart_window_ctl import open_service_window
from .service_config import cdp_ask_url_config

logger = get_logger(__name__)

# Vocabulary aligned with the MCP drain contract (middleware/drain.py).
RETRY_AFTER_S = 30
_PROBE_TIMEOUT_S = 5.0
# The probe must reach the *local host* Stargate admin endpoint — the host
# client-facing port (topology: :9999). Deliberately NOT
# transport_utils.DEFAULT_STARGATE_URL: that resolves STARGATE_UNIX_SOCKET →
# STARGATE_URL → localhost first, an order meant for container callers routing
# into the edge. If either var is ever exported in the ./manage shell the probe
# would target the wrong endpoint and every non-force stargate restart would
# return state=probe_error (perpetual deferral). Pin the host port explicitly.
STARGATE_PROBE_URL = f"http://localhost:{os.environ.get('STARGATE_PORT', '9999')}"
GIT_INTEGRATION_WORKER_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091"
)

# Only these actions are drain-gated. start/status/health/wait_healthy never kill
# live work; rebuild routes through sync_restart for the relevant services.
GATED_ACTIONS = frozenset({"stop", "restart", "sync_restart"})


@dataclass(slots=True, kw_only=True)
class ActiveWork:
    """Snapshot from a service's active-work probe."""

    busy: bool
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, kw_only=True)
class DrainOutcome:
    """A deferral outcome — the restart did NOT proceed.

    state ∈ {"busy", "in_progress", "probe_error"}.
    """

    state: str
    service: str
    reason: str
    retry_after_s: int = RETRY_AFTER_S
    active_work: dict[str, Any] = field(default_factory=dict)

    def to_result(self) -> dict[str, Any]:
        """Render the JSON-RPC result dict returned over manage.sock."""
        from .busy_work_summary import format_active_work_summary

        return {
            "status": "deferred",
            "state": self.state,
            "service": self.service,
            "reason": self.reason,
            "retry_after_s": self.retry_after_s,
            "active_work": self.active_work,
            "active_work_summary": format_active_work_summary(self.active_work),
        }


@runtime_checkable
class BusyProbe(Protocol):
    """Strategy: report whether a service has in-flight work."""

    async def snapshot(self) -> ActiveWork: ...


class NullBusyProbe:
    """Probe for services with no long-running, cancel-on-restart work.

    Used for sub-second request services (cortex_api, agent_bus, event_service).
    MCP is **not** NullBusyProbe — see ``mcp_restart_probe.McpBusyProbe``; the
    container's SIGTERM HTTP drain alone does not cover Cowork life sessions.
    """

    async def snapshot(self) -> ActiveWork:
        return ActiveWork(busy=False)


class HttpActiveWorkProbe:
    """Probe an HTTP busy-state endpoint and preserve its detail for drain policy consumers without blocking census work."""

    def __init__(self, base_url: str, path: str) -> None:
        self._base_url = base_url
        self._path = path

    async def snapshot(self) -> ActiveWork:
        async with make_async_client(
            self._base_url, timeout=_PROBE_TIMEOUT_S
        ) as client:
            resp = await client.get(self._path)
            resp.raise_for_status()
            data = resp.json()
        if not isinstance(data, dict):
            raise ValueError(f"active-work probe returned non-object: {type(data)!r}")
        return ActiveWork(busy=bool(data.get("busy", False)), detail=data)


def _default_probes() -> dict[str, BusyProbe]:
    """Service → busy probe. Unlisted services default to NullBusyProbe."""
    from .mcp_restart_probe import build_mcp_busy_probe

    probes: dict[str, BusyProbe] = {
        "stargate": HttpActiveWorkProbe(
            STARGATE_PROBE_URL, "/api/v1/admin/active-work"
        ),
        "git_integration_worker": HttpActiveWorkProbe(
            GIT_INTEGRATION_WORKER_URL, "/api/v1/git/active-work"
        ),
        "mcp": build_mcp_busy_probe(),
    }
    cfg = cdp_ask_url_config()
    if cfg is not None:
        _host, _port, base = cfg
        probes["cdp_ask"] = HttpActiveWorkProbe(
            base, "/v1/project-ask/drain-state"
        )
    return probes


class RestartDrainGate:
    """Per-service restart mutex + busy-probe drain check.

    One instance is owned by ServiceController so the per-service gates persist
    across manage calls (coalescing requires shared state).
    """

    def __init__(self, probes: dict[str, BusyProbe] | None = None) -> None:
        self._probes: dict[str, BusyProbe] = (
            probes if probes is not None else _default_probes()
        )
        self._gates: dict[str, FifoCapacityGate] = {}

    def _gate(self, service: str) -> FifoCapacityGate:
        gate = self._gates.get(service)
        if gate is None:
            gate = FifoCapacityGate(limit=1, gate_id=f"restart:{service}")
            self._gates[service] = gate
        return gate

    def _probe(self, service: str) -> BusyProbe:
        return self._probes.get(service, NullBusyProbe())

    async def evaluate(self, service: str, *, force: bool) -> DrainOutcome | None:
        """Decide whether a restart may proceed.

        Returns:
            None — proceed; the restart-mutex slot is HELD. The caller MUST call
                ``release(service)`` once the stop/start cycle finishes.
            DrainOutcome — deferred; no slot is held. The caller returns the
                outcome and does NOT call release.
        """
        gate = self._gate(service)
        if not gate.try_acquire(str(uuid.uuid4())):
            return DrainOutcome(
                state="in_progress",
                service=service,
                reason="a restart is already in progress for this service",
            )

        # Slot is now HELD. Every exit that is not an explicit proceed must release
        # it — including unexpected exceptions (e.g. asyncio.CancelledError on manage
        # teardown), which the finally releases before they re-propagate. Otherwise
        # the slot leaks and the service can never be restarted for the process'
        # lifetime.
        proceed = False
        try:
            if force:
                logger.info("restart of %s forced; skipping drain check", service)
                proceed = True
                return None  # slot held; proceed

            try:
                work = await self.probe(service)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                # Probe failure must not kill a maybe-busy service. Fail closed: defer.
                logger.warning("active-work probe failed for %s: %s", service, exc)
                return DrainOutcome(
                    state="probe_error",
                    service=service,
                    reason=f"could not determine in-flight work: {exc}",
                )

            if work.busy:
                return DrainOutcome(
                    state="busy",
                    service=service,
                    reason="service has in-flight work; retry later or pass force=true",
                    active_work=work.detail,
                )

            proceed = True
            return None  # slot held; proceed
        finally:
            if not proceed:
                await gate.release()

    async def release(self, service: str) -> None:
        """Release the restart-mutex slot held by a proceeding restart."""
        await self._gate(service).release()

    async def probe(self, service: str) -> ActiveWork:
        """Run a service's busy probe WITHOUT acquiring the restart slot.

        Single shared probe call site: both ``evaluate`` (acquiring path) and
        ``busy_report`` (read-only path) reach the probe through here, so there
        is exactly one place that invokes ``BusyProbe.snapshot`` — no second
        probe implementation. Probe exceptions propagate to the caller, which
        decides how to render them (``evaluate`` → ``state=probe_error`` deferral;
        ``busy_report`` → ``restart_would_defer=True`` with an error detail).
        """
        return await self._probe(service).snapshot()

    def restart_in_progress(self, service: str) -> bool:
        """True iff the per-service restart slot is currently held (no free slot).

        Read-only: inspects gate occupancy without acquiring, so the busy read
        model can set ``restart_would_defer`` for a service whose restart is
        already in flight — mirroring the ``state="in_progress"`` deferral that
        ``evaluate`` would return for a concurrent caller.
        """
        gate = self._gate(service)
        return gate.active_count >= gate.current_limit

    async def busy_report(self, services: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Per-service busy read model (pull). Probes WITHOUT acquiring any slot.

        For each service, returns
        ``{"busy": bool, "restart_would_defer": bool, "active_work": {...}}``.

        ``restart_would_defer`` ⟺ ``busy`` ∨ a restart is already in progress ∨
        the probe failed. Probe failure is reported as ``busy=False`` with
        ``restart_would_defer=True`` (fail closed: a non-force restart would
        defer with ``state=probe_error``) and an ``error`` entry in
        ``active_work`` — identical fail-closed posture to ``evaluate``.
        """
        report: dict[str, dict[str, Any]] = {}
        for service in services:
            in_progress = self.restart_in_progress(service)
            try:
                work = await self.probe(service)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                report[service] = {
                    "busy": False,
                    "restart_would_defer": True,
                    "active_work": {"error": str(exc)},
                }
                continue
            report[service] = {
                "busy": work.busy,
                "restart_would_defer": work.busy or in_progress,
                "active_work": work.detail,
            }
        return report


async def run_gated(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    force: bool,
    lifecycle: Callable[[], Awaitable[str]],
) -> dict[str, Any]:
    """Run one lifecycle action under the drain gate. Single shared entry point.

    Both the MCP dispatch path (``api_dispatch.execute``) and the TUI workers
    (``view/screens/services.py``) call this so the gate sits at the real shared
    chokepoint (ServiceController) and a busy/in-flight restart is deferred — and
    coalesced — identically regardless of caller.

    ``lifecycle`` is a zero-arg coroutine factory that performs the actual
    stop/start work and returns the human-readable message.

    Returns:
        ``{"status": "ok", "message": <lifecycle message>}`` when the action ran,
        or ``DrainOutcome.to_result()`` (``{"status": "deferred", ...}``) when the
        gate deferred. Non-gated actions run the lifecycle without touching the gate.
    """
    if action not in GATED_ACTIONS:
        return {"status": "ok", "message": await lifecycle()}
    outcome = await gate.evaluate(service, force=force)
    if outcome is not None:
        return outcome.to_result()
    try:
        message = await lifecycle()
    finally:
        await gate.release(service)
    return {"status": "ok", "message": message}


BackgroundCompleteHook = Callable[[str, float], Awaitable[None]]
BackgroundFailedHook = Callable[[str, float], Awaitable[None]]

# Strong refs to in-flight deferred-restart tasks. asyncio holds only a weak
# reference to a bare create_task() result; without this the task can be GC'd
# mid-flight, and since _background's finally is the sole release of the held
# restart-mutex slot, a dropped task leaks the slot for the process lifetime.
_DEFERRED_RESTART_TASKS: set[asyncio.Task[None]] = set()


async def run_gated_deferred(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    force: bool,
    lifecycle: Callable[[], Awaitable[str]],
    scheduled_message: str,
    on_background_complete: BackgroundCompleteHook | None = None,
    on_background_failed: BackgroundFailedHook | None = None,
) -> dict[str, Any]:
    """Acquire the restart gate, schedule lifecycle in background, return immediately.

    Used for MCP ``sync_restart`` / ``rebuild`` so the manage.sock JSON-RPC response
    is flushed before the MCP container is stopped (the triggering MCP tool call
    otherwise dies with the container). The caller MUST still pass a gated action
    (``stop``, ``restart``, ``sync_restart``); ``evaluate`` + ``release`` discipline
    matches ``run_gated``, but lifecycle runs in ``asyncio.create_task``.
    """
    if action not in GATED_ACTIONS:
        return {"status": "ok", "message": await lifecycle()}
    outcome = await gate.evaluate(service, force=force)
    if outcome is not None:
        return outcome.to_result()

    async def _background() -> None:
        t0 = time.monotonic()
        try:
            message = await lifecycle()
            duration_s = time.monotonic() - t0
            logger.info(
                "deferred %s %s completed in %.1fs: %s",
                action,
                service,
                duration_s,
                message[:200],
            )
            if on_background_complete is not None:
                await on_background_complete(message, duration_s)
        except Exception as exc:
            duration_s = time.monotonic() - t0
            logger.exception(
                "deferred %s %s failed after %.1fs: %s",
                action,
                service,
                duration_s,
                exc,
            )
            if on_background_failed is not None:
                await on_background_failed(str(exc), duration_s)
        finally:
            await gate.release(service)

    task = asyncio.create_task(_background())
    _DEFERRED_RESTART_TASKS.add(task)
    task.add_done_callback(_DEFERRED_RESTART_TASKS.discard)
    return {"status": "ok", "message": scheduled_message}


# ---------------------------------------------------------------------------
# git-integration-worker event-driven deferred restart (Phase 2)
# ---------------------------------------------------------------------------
# A git-worker-specific branch: instead of the busy-probe deferral, manage holds
# the restart-mutex slot, persists a durable restart intent, kicks off the worker
# drain, and hands the rest to an async drain supervisor. The slot is released by
# the supervise task's finally (mirrors run_gated_deferred). The generic
# run_gated/run_gated_deferred contract above is untouched for every other service.

# Strong refs to in-flight supervise tasks — a dropped task leaks the held
# restart-mutex slot (the supervise finally is the sole release of that slot).
_SUPERVISE_TASKS: set[asyncio.Task[None]] = set()


def _drain_deferred_result(
    intent: Any,
    *,
    reason: str | None = None,
    activation_validation_id: str | None = None,
) -> dict[str, Any]:
    """The 202 envelope for a deferred, drain-supervised git-worker restart.

    ``caller_must_exit_to_release_lease`` is binding for cursor-sdk holders:
    waiting in-window for healthy while holding ``cursor_sdk_gate`` never
    converges (friction 25989). Arm the intent, then exit so active_count→0.
    Activation proof is supervisor-owned after exit.
    """
    from scripts.model_manager.ui.controller.restart_intent_consumer import (
        project_restart_intent_consumer,
    )

    projected = project_restart_intent_consumer(intent)
    result = {
        "status": "deferred",
        "state": "draining",
        "service": intent.service,
        "restart_intent_id": projected["restart_intent_id"],
        "deadline_ceiling_at": projected["deadline_ceiling_at"],
        "deadline_semantics": projected["deadline_semantics"],
        "deadline_at": projected["deadline_at"],
        "reason": reason or "draining; completion delivered via git_worker.drain events",
        "caller_must_exit_to_release_lease": True,
        "guidance": (
            "If you hold the git_integration_worker write lease (cursor-sdk), "
            "exit this dispatch now — do not wait_healthy in-window. "
            "Activation proof is supervisor-owned; query via activation_validation_id "
            "or fleet_liveness(code_ref=…)."
        ),
    }
    if activation_validation_id is not None:
        result["activation_validation_id"] = activation_validation_id
    return result


def _blocking_drain_result(
    *, service: str, action: str, intent_id: str, final: Any
) -> dict[str, Any]:
    """Terminal envelope for the fleet blocking path (ok vs error)."""
    drained_ok = final is not None and final.status in {
        "completed",
        "verifying_activation",
        "activation_unverified",
    }
    return {
        "status": "ok" if drained_ok else "error",
        "drain_status": (final.status if final is not None else "missing"),
        "service": service,
        "action": action,
        "restart_intent_id": intent_id,
    }


def _mint_activation_validation(store: Any, intent: Any) -> str:
    from charter_runner_store.propagation_validation import (
        latest_validation_for_intent,
        mint_pending_validation_for_intent,
    )

    existing = latest_validation_for_intent(intent.intent_id)
    if existing is not None and existing.outcome == "pending":
        return existing.validation_id
    return mint_pending_validation_for_intent(
        intent,
        advance_intent_fn=store.advance_if_status,
    )


async def _await_intent_terminal(
    store: Any, intent: Any, *, deadline_s: float
) -> Any:
    """Poll until ``intent`` leaves non-terminal statuses or ``deadline_s`` elapses.

    Used when the blocking path coalesces onto a live intent owned by another
    supervise task — we must not return a deferred envelope (fleet would score
    that as stop failure) and must not start a second begin-drain.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + deadline_s
    intent_id = intent.intent_id
    while True:
        current = store.get(intent_id)
        if current is None:
            return None
        if current.status not in ("pending_drain", "drained_restarting"):
            return current
        if loop.time() >= deadline:
            return current
        await asyncio.sleep(0.2)


def _spawn_supervised(
    gate: RestartDrainGate, service: str, supervisor: Any, intent: Any
) -> None:
    """Schedule supervise(intent) as a tracked task; release the slot in finally."""

    async def _background() -> None:
        try:
            await supervisor.supervise(intent)
        finally:
            await gate.release(service)

    task = asyncio.create_task(_background())
    _SUPERVISE_TASKS.add(task)
    task.add_done_callback(_SUPERVISE_TASKS.discard)


async def run_gated_drain_supervised(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    store: Any,
    supervisor: Any,
    reason: str,
) -> dict[str, Any]:
    """git-worker non-force stop/restart/sync_restart → durable drain supervision.

    Busy-skip (``evaluate(force=True)``): in-flight cursor-sdk/integrate work must
    **not** soft-defer without an intent. The supervisor begins drain and waits
    for ``active_count→0`` (same posture as the fleet blocking path). Soft
    ``state=busy`` with no durable intent is the self-held lease deadlock
    (friction 25989 / todo:manage-busy-drain-restart).

    Coalescing (AC-6): if the restart-mutex slot is already held by an in-flight
    supervision, the existing live intent's id is returned — no second intent, no
    second begin-drain.
    """
    outcome = await gate.evaluate(service, force=True)
    if outcome is not None:
        existing = store.active_for_service(service)
        if existing is not None:
            validation_id = _mint_activation_validation(store, existing)
            return _drain_deferred_result(
                existing,
                reason="drain already in progress for this service",
                activation_validation_id=validation_id,
            )
        return outcome.to_result()

    deadline_at = (
        datetime.now(UTC) + timedelta(seconds=supervisor.deadline_s)
    ).isoformat()
    try:
        intent = store.create_intent(
            service=service, action=action, deadline_at=deadline_at, reason=reason
        )
        validation_id = _mint_activation_validation(store, intent)
    except Exception:
        # Never leak the held slot if the durable write fails.
        await gate.release(service)
        raise
    await open_service_window(
        store,
        service,
        reason=f"git-worker drain {action}",
    )
    _spawn_supervised(gate, service, supervisor, intent)
    return _drain_deferred_result(intent, activation_validation_id=validation_id)


async def run_gated_drain_supervised_blocking(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    store: Any,
    supervisor: Any,
    reason: str,
) -> dict[str, Any]:
    """Like ``run_gated_drain_supervised`` but AWAIT the drain to terminal state.

    Fleet stop/restart cycles must not return until the worker has drained and
    been killed, so a later fleet START phase does not race the supervisor
    (Phase-3 fleet re-enable — decision on todo:git-worker-drain-p3-fleet,
    gpt-5.5 review thread 2018).

    Busy-skip: ``evaluate(..., force=True)`` skips the active-work probe so
    in-flight integrate/cursor-sdk work does not defer the fleet stop. The
    supervisor still begins drain and waits for ``active_count→0`` before
    SIGTERM. ``force`` here means busy-probe skip only — not the API/MCP
    ``force=true`` immediate-kill path.

    Coalescing: if the restart-mutex slot is already held, await the live
    intent to a terminal status (do not return a deferred envelope — fleet
    scores that as stop failure).
    """
    outcome = await gate.evaluate(service, force=True)
    if outcome is not None:
        existing = store.active_for_service(service)
        if existing is None:
            return outcome.to_result()
        final = await _await_intent_terminal(
            store, existing, deadline_s=float(supervisor.deadline_s)
        )
        return _blocking_drain_result(
            service=service,
            action=action,
            intent_id=existing.intent_id,
            final=final,
        )

    deadline_at = (
        datetime.now(UTC) + timedelta(seconds=supervisor.deadline_s)
    ).isoformat()
    try:
        intent = store.create_intent(
            service=service, action=action, deadline_at=deadline_at, reason=reason
        )
    except Exception:
        await gate.release(service)
        raise

    await open_service_window(
        store,
        service,
        reason=f"git-worker fleet drain {action}",
    )

    try:
        await supervisor.supervise(intent)
    finally:
        await gate.release(service)
        from .restart_window_ctl import clear_service_windows

        await clear_service_windows(
            store, service, reason="git-worker supervised drain completed"
        )

    return _blocking_drain_result(
        service=service,
        action=action,
        intent_id=intent.intent_id,
        final=store.get(intent.intent_id),
    )


async def resume_drain_supervision(
    gate: RestartDrainGate, service: str, *, supervisor: Any, intent: Any
) -> None:
    """Startup reconcile: resume a persisted pending intent on a fresh supervisor.

    Acquires the slot without a busy-probe (force=True → evaluate returns None,
    slot held) and schedules the supervisor; begin-drain is idempotent and the
    subscription resumes from the stored last_seen_event_seq, so this never
    duplicates a kill. A slot already held (concurrent resume) is a no-op.
    """
    outcome = await gate.evaluate(service, force=True)
    if outcome is not None:
        return
    _spawn_supervised(gate, service, supervisor, intent)
