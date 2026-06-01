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

import os
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx
from transport_utils import make_async_client
from universal_concurrency import FifoCapacityGate
from universal_logging import get_logger

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
        return {
            "status": "deferred",
            "state": self.state,
            "service": self.service,
            "reason": self.reason,
            "retry_after_s": self.retry_after_s,
            "active_work": self.active_work,
        }


@runtime_checkable
class BusyProbe(Protocol):
    """Strategy: report whether a service has in-flight work."""

    async def snapshot(self) -> ActiveWork: ...


class NullBusyProbe:
    """Probe for services with no long-running, cancel-on-restart work.

    Used for services that either self-drain on SIGTERM (mcp container stop -t 30)
    at a layer below the gate, or whose requests are
    sub-second (cortex_api, agent_bus, event_service).
    """

    async def snapshot(self) -> ActiveWork:
        return ActiveWork(busy=False)


class HttpActiveWorkProbe:
    """Probe an HTTP ``active-work`` endpoint returning ``{"busy": bool, ...}``."""

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
    return {
        "stargate": HttpActiveWorkProbe(
            STARGATE_PROBE_URL, "/api/v1/admin/active-work"
        ),
        "git_integration_worker": HttpActiveWorkProbe(
            GIT_INTEGRATION_WORKER_URL, "/api/v1/git/active-work"
        ),
    }


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
