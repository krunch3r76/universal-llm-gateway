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

from services.git_integration_worker import (
    cursor_sdk_restart_bridge_gate as bridge_gate,
)

from .git_worker_activation_verify import mint_activation_validation
from .restart_intent_consumer import blocking_drain_result, drain_deferred_result
from .restart_window_ctl import open_service_window
from .service_config import cdp_ask_url_config

_drain_deferred_result = drain_deferred_result

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

_SELF_HOLDER_POLL_INTERVAL_S = 0.2


def holder_dispatch_id_from_active_work(active_work: dict[str, Any]) -> str | None:
    """Extract the active write-lease holder dispatch id from a probe payload."""
    lease = active_work.get("write_lease")
    if isinstance(lease, dict):
        holder = lease.get("holder_dispatch_id")
        if isinstance(holder, str) and holder.strip():
            return holder.strip()
    gate = active_work.get("cursor_sdk_gate")
    if isinstance(gate, dict):
        busy = gate.get("busy_status")
        if isinstance(busy, dict):
            active_holder = busy.get("active_holder")
            if isinstance(active_holder, dict):
                holder = active_holder.get("dispatch_id")
                if isinstance(holder, str) and holder.strip():
                    return holder.strip()
    return None


def sole_busy_holder_matches(
    active_work: dict[str, Any], caller_dispatch_id: str
) -> bool:
    """True when ``caller_dispatch_id`` is the only busy gate holder in the probe."""
    caller = caller_dispatch_id.strip()
    if not caller:
        return False
    holder = holder_dispatch_id_from_active_work(active_work)
    if holder != caller:
        return False
    active_count = active_work.get("active_count")
    if isinstance(active_count, int) and active_count != 1:
        return False
    lease = active_work.get("write_lease")
    if isinstance(lease, dict):
        queue_depth = lease.get("queue_depth")
        if isinstance(queue_depth, int) and queue_depth > 0:
            return False
    gate = active_work.get("cursor_sdk_gate")
    if isinstance(gate, dict):
        busy = gate.get("busy_status")
        if isinstance(busy, dict):
            queue_depth = busy.get("queue_depth")
            if isinstance(queue_depth, int) and queue_depth > 0:
                return False
    queued = active_work.get("queued")
    if isinstance(queued, int) and queued > 0:
        return False
    return True


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
        "agent_bus": HttpActiveWorkProbe(
            GIT_INTEGRATION_WORKER_URL, "/api/v1/git/active-work"
        ),
        "mcp": build_mcp_busy_probe(),
    }
    cfg = cdp_ask_url_config()
    if cfg is not None:
        _host, _port, base = cfg
        probes["cdp_ask"] = HttpActiveWorkProbe(base, "/v1/project-ask/drain-state")
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

    async def evaluate(
        self, service: str, *, force: bool, supervised_drain: bool = False
    ) -> DrainOutcome | None:
        """Decide whether a restart may proceed.

        ``supervised_drain`` marks the durable git-worker drain path: it takes
        the mutex like ``force`` (no busy probe — the supervisor owns occupancy)
        but is **not** an immediate kill, so the live-bridge refusal that
        protects operator bridges from ``force=true`` does not apply. The drain
        keep-awaits (or parks, ``park_live``) those bridges instead; refusing
        to arm while they are live would make park-for-restart unreachable
        exactly when it is needed (steer-restart v1 AMEND-B).

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
                if service == "git_integration_worker" and not supervised_drain:
                    if bridge_gate.live_bridge_blocks_restart(force=True):
                        bridge_gate.defer_restart_for_live_bridges(force=True)
                        return DrainOutcome(
                            state="draining",
                            service=service,
                            reason=(
                                "git_integration_worker has live operator bridges; "
                                "force restart deferred until bridges exit"
                            ),
                        )
                if not supervised_drain:
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


_SUPERVISE_TASKS: set[asyncio.Task[None]] = set()


async def _await_intent_terminal(store: Any, intent: Any, *, deadline_s: float) -> Any:
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
    async def _background() -> None:
        released = False

        async def release_once() -> None:
            nonlocal released
            if released:
                return
            released = True
            await gate.release(service)

        supervisor.on_timeout_mutex_release = release_once
        try:
            await supervisor.supervise(intent)
        finally:
            await release_once()

    task = asyncio.create_task(_background())
    _SUPERVISE_TASKS.add(task)
    task.add_done_callback(_SUPERVISE_TASKS.discard)


@dataclass(slots=True, kw_only=True)
class LocalServiceDrainSupervisor:
    """Wait for a self-holder cursor-sdk dispatch to exit, then run lifecycle."""

    gate: RestartDrainGate
    service: str
    store: Any
    lifecycle: Callable[[], Awaitable[str]]
    deadline_s: float = 604800.0
    poll_interval_s: float = _SELF_HOLDER_POLL_INTERVAL_S

    async def supervise(self, intent: Any) -> None:
        from .restart_intent_states import (
            STATUS_COMPLETED,
            STATUS_DRAINED_RESTARTING,
            STATUS_FAILED,
            STATUS_TIMEOUT,
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.deadline_s
        intent_id = intent.intent_id
        try:
            while loop.time() < deadline:
                work = await self.gate.probe(self.service)
                if not work.busy:
                    break
                await asyncio.sleep(self.poll_interval_s)
            else:
                self.store.advance(intent_id, status=STATUS_TIMEOUT)
                return
            self.store.advance(intent_id, status=STATUS_DRAINED_RESTARTING)
            await self.lifecycle()
            self.store.advance(intent_id, status=STATUS_COMPLETED)
        except Exception:
            current = self.store.get(intent_id)
            if current is not None and current.status not in {
                STATUS_COMPLETED,
                STATUS_TIMEOUT,
            }:
                self.store.advance(intent_id, status=STATUS_FAILED)
            raise


async def run_gated_self_holder_drain_supervised(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    store: Any,
    supervisor: Any,
    reason: str,
    caller_dispatch_id: str,
    code_ref: str = "HEAD",
    row_id: str | None = None,
) -> dict[str, Any]:
    """Arm a durable restart intent when the sole busy holder is the caller dispatch."""
    work = await gate.probe(service)
    if not work.busy or not sole_busy_holder_matches(work.detail, caller_dispatch_id):
        outcome = await gate.evaluate(service, force=False)
        if outcome is not None:
            return outcome.to_result()
        return {
            "status": "error",
            "service": service,
            "reason": "self_holder_drain_precondition_failed",
        }

    outcome = await gate.evaluate(service, force=True, supervised_drain=True)
    if outcome is not None:
        existing = store.active_for_service(service)
        if existing is not None:
            validation_id = mint_activation_validation(
                store, existing, code_ref=code_ref, row_id=row_id
            )
            return drain_deferred_result(
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
            service=service,
            action=action,
            deadline_at=deadline_at,
            reason=reason,
        )
        validation_id = mint_activation_validation(
            store, intent, code_ref=code_ref, row_id=row_id
        )
    except Exception:
        await gate.release(service)
        raise
    await open_service_window(store, service, reason=f"self-holder drain {action}")
    _spawn_supervised(gate, service, supervisor, intent)
    return drain_deferred_result(intent, activation_validation_id=validation_id)


async def run_gated_drain_supervised(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    store: Any,
    supervisor: Any,
    reason: str,
    code_ref: str = "HEAD",
    row_id: str | None = None,
    park_live: bool = False,
) -> dict[str, Any]:
    """Arm a durable git-worker drain intent and return the deferred 202 envelope.

    ``park_live`` (steer-restart v1) makes the supervisor park live cursor-sdk
    dispatches at drain start instead of keep-awaiting them.
    """
    outcome = await gate.evaluate(service, force=True, supervised_drain=True)
    if outcome is not None:
        existing = store.active_for_service(service)
        if existing is not None:
            validation_id = mint_activation_validation(
                store, existing, code_ref=code_ref, row_id=row_id
            )
            return drain_deferred_result(
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
            service=service,
            action=action,
            deadline_at=deadline_at,
            reason=reason,
            park_live=park_live,
        )
        validation_id = mint_activation_validation(
            store, intent, code_ref=code_ref, row_id=row_id
        )
    except Exception:
        await gate.release(service)
        raise
    await open_service_window(store, service, reason=f"git-worker drain {action}")
    _spawn_supervised(gate, service, supervisor, intent)
    return drain_deferred_result(intent, activation_validation_id=validation_id)


async def run_gated_drain_supervised_blocking(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    store: Any,
    supervisor: Any,
    reason: str,
) -> dict[str, Any]:
    """Await supervised drain to a terminal intent status before returning to fleet."""
    outcome = await gate.evaluate(service, force=True, supervised_drain=True)
    if outcome is not None:
        existing = store.active_for_service(service)
        if existing is None:
            return outcome.to_result()
        final = await _await_intent_terminal(
            store, existing, deadline_s=float(supervisor.deadline_s)
        )
        return blocking_drain_result(
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
    await open_service_window(store, service, reason=f"git-worker fleet drain {action}")
    try:
        await supervisor.supervise(intent)
    finally:
        await gate.release(service)
        from .restart_window_ctl import clear_service_windows

        await clear_service_windows(
            store, service, reason="git-worker supervised drain completed"
        )
    return blocking_drain_result(
        service=service,
        action=action,
        intent_id=intent.intent_id,
        final=store.get(intent.intent_id),
    )


async def resume_drain_supervision(
    gate: RestartDrainGate, service: str, *, supervisor: Any, intent: Any
) -> None:
    """Resume a persisted pending intent after manage process restart."""
    outcome = await gate.evaluate(service, force=True, supervised_drain=True)
    if outcome is not None:
        return
    _spawn_supervised(gate, service, supervisor, intent)


__all__ = [
    "ActiveWork",
    "BackgroundCompleteHook",
    "BackgroundFailedHook",
    "BusyProbe",
    "DrainOutcome",
    "GATED_ACTIONS",
    "GIT_INTEGRATION_WORKER_URL",
    "HttpActiveWorkProbe",
    "LocalServiceDrainSupervisor",
    "NullBusyProbe",
    "RETRY_AFTER_S",
    "RestartDrainGate",
    "STARGATE_PROBE_URL",
    "holder_dispatch_id_from_active_work",
    "resume_drain_supervision",
    "run_gated",
    "run_gated_deferred",
    "run_gated_drain_supervised",
    "run_gated_drain_supervised_blocking",
    "run_gated_self_holder_drain_supervised",
    "sole_busy_holder_matches",
]
