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
from .restart_drain_witness import (
    InFlightWitness,
    LastProbeWitness,
    in_flight_defer_reason,
    resolve_probe_failure,
)
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
# Operator-proxy propagate (inv 39): CDP generates have no GIW park path — after
# this ceiling the supervisor runs sync_restart while requests may still be in
# flight (self-preempt force). Idle predicate reads Stargate ``/admin/active-work``
# ``busy`` (``requests_in_flight`` + async pipelines).
STARGATE_OPERATOR_IDLE_CEILING_S = 600.0
GIT_INTEGRATION_WORKER_URL = os.environ.get(
    "GIT_INTEGRATION_WORKER_URL", "http://127.0.0.1:8091"
)

# Only these actions are drain-gated. start/status/health/wait_healthy never kill
# live work; rebuild routes through sync_restart for the relevant services.
GATED_ACTIONS = frozenset({"stop", "restart", "sync_restart"})

_SELF_HOLDER_POLL_INTERVAL_S = 0.2


def describe_probe_exc(exc: BaseException) -> str:
    """Render a probe failure with its class; httpx timeouts often stringify to ``""``."""
    text = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {text}" if text else name


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


_CALLER_ID_KEYS = ("op_id", "job_id", "dispatch_id")


def _identity_values(row: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in _CALLER_ID_KEYS:
        val = row.get(key)
        if isinstance(val, str) and val.strip():
            found.add(val.strip())
    return found


def _holder_record(row: dict[str, Any], *, kind: str | None = None) -> dict[str, Any]:
    ident = row.get("op_id") or row.get("dispatch_id") or row.get("job_id")
    rec: dict[str, Any] = {"kind": kind or str(row.get("kind") or "op")}
    if isinstance(ident, str) and ident.strip():
        rec["op_id"] = ident.strip()
    subject = row.get("subject_preview")
    if isinstance(subject, str) and subject.strip():
        rec["subject_preview"] = subject.strip()
    return rec


def exclude_caller_from_agent_bus_census(
    detail: dict[str, Any],
    *,
    exclude_job_id: str,
) -> ActiveWork:
    """Drop the propagating cursor-auto job from an agent_bus busy snapshot.

    GIW ``busy`` is ``active_count > 0``. ``active_ops`` unions claimed Auto
    jobs, so the job executing ``contract:propagate`` is itself the occupant.
    Counting it defers the restart until that job exits, and the job cannot
    exit until manage returns. Foreign ops, a foreign write-lease holder, and
    ``live_bridges`` rows stay. When the caller id is not in the snapshot and
    the raw ``busy`` bit is set, the result stays busy (fail closed).

    Does not change GIW's own busy flag — apply this only on the agent_bus path.
    """
    caller = exclude_job_id.strip()
    raw_ops = detail.get("active_ops")
    remaining: list[Any] = []
    dropped = False
    if isinstance(raw_ops, list):
        for op in raw_ops:
            if isinstance(op, dict) and caller in _identity_values(op):
                dropped = True
                continue
            remaining.append(op)

    holders: list[dict[str, Any]] = []
    seen: set[str] = set()
    for op in remaining:
        if not isinstance(op, dict):
            continue
        rec = _holder_record(op)
        ident = rec.get("op_id")
        if isinstance(ident, str):
            seen.add(ident)
        holders.append(rec)

    lease = detail.get("write_lease")
    if isinstance(lease, dict):
        hid = lease.get("holder_dispatch_id")
        if isinstance(hid, str) and hid.strip() and hid.strip() != caller:
            if hid.strip() not in seen:
                holders.append(
                    {
                        "kind": "write_lease",
                        "op_id": hid.strip(),
                        "dispatch_id": hid.strip(),
                    }
                )
                seen.add(hid.strip())

    bridges = detail.get("live_bridges")
    if isinstance(bridges, list):
        for bridge in bridges:
            if not isinstance(bridge, dict):
                continue
            if caller in _identity_values(bridge):
                dropped = True
                continue
            rec = _holder_record(bridge, kind=str(bridge.get("kind") or "live_bridge"))
            ident = rec.get("op_id")
            if isinstance(ident, str) and ident in seen:
                continue
            if isinstance(ident, str):
                seen.add(ident)
            holders.append(rec)

    if holders:
        busy = True
    elif dropped:
        busy = False
    else:
        busy = bool(detail.get("busy", False))

    filtered = dict(detail)
    if isinstance(raw_ops, list):
        filtered["active_ops"] = remaining
        filtered["active_count"] = len(remaining)
    filtered["busy"] = busy
    filtered["excluded_job_id"] = caller
    if holders:
        filtered["holders"] = holders
    return ActiveWork(busy=busy, detail=filtered)


def agent_bus_restart_would_defer(
    detail: dict[str, Any],
    *,
    exclude_job_id: str,
    restart_in_progress: bool = False,
) -> dict[str, Any]:
    """agent_bus drain decision after dropping the propagating cursor-auto job.

    ``restart_would_defer`` matches ``RestartDrainGate.busy_report``:
    filtered busy, or a restart already in progress. ``deferral`` is the
    manage payload when the filtered census is still busy, else None.
    """
    work = exclude_caller_from_agent_bus_census(detail, exclude_job_id=exclude_job_id)
    would_defer = work.busy or restart_in_progress
    deferral: dict[str, Any] | None = None
    if work.busy:
        deferral = DrainOutcome(
            state="busy",
            service="agent_bus",
            reason=in_flight_defer_reason(work.detail),
            active_work=work.detail,
        ).to_result()
    return {
        "busy": work.busy,
        "restart_would_defer": would_defer,
        "active_work": work.detail,
        "deferral": deferral,
    }


@dataclass(slots=True, kw_only=True)
class ActiveWork:
    """Snapshot from a service's active-work probe."""

    busy: bool
    detail: dict[str, Any] = field(default_factory=dict)


# Structured discriminator for a codeblind caller. ``probe_error`` is not a
# busy finding; the reason string is prose and is not this field.
_DETERMINATION_BY_STATE = {
    "busy": "busy",
    "in_progress": "in_progress",
    "probe_error": "undetermined",
}


@dataclass(slots=True, kw_only=True)
class DrainOutcome:
    """A deferral outcome — the restart did NOT proceed.

    state ∈ {"busy", "in_progress", "probe_error"}.
    ``to_result`` adds ``determination`` so a caller can tell a busy finding
    from a probe that did not answer without reading ``reason``.
    """

    state: str
    service: str
    reason: str
    retry_after_s: int = RETRY_AFTER_S
    active_work: dict[str, Any] = field(default_factory=dict)

    def to_result(self) -> dict[str, Any]:
        """Render the JSON-RPC result dict returned over manage.sock."""
        from .busy_work_summary import format_active_work_summary

        result = {
            "status": "deferred",
            "state": self.state,
            "service": self.service,
            "reason": self.reason,
            "retry_after_s": self.retry_after_s,
            "active_work": self.active_work,
            "active_work_summary": format_active_work_summary(self.active_work),
        }
        determination = _DETERMINATION_BY_STATE.get(self.state)
        if determination is not None:
            result["determination"] = determination
        return result


@runtime_checkable
class BusyProbe(Protocol):
    """Strategy: report whether a service has in-flight work."""

    async def snapshot(self) -> ActiveWork: ...


class NullBusyProbe:
    """Probe for services with no long-running, cancel-on-restart work.

    Used for sub-second request services (cortex_api, event_service).
    ``agent_bus`` is **not** this probe — ``_default_probes`` points it at
    GIW ``/api/v1/git/active-work``. MCP is **not** NullBusyProbe either —
    see ``mcp_restart_probe.McpBusyProbe``; the container's SIGTERM HTTP
    drain alone does not cover Cowork life sessions.
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


class JobsBusyProbe:
    """Defer restart while a bus-reply-watch fold is still open.

    Reads ``journal.db`` through ``jobs.busy``. A missing database is idle.
    Other job names do not hold the drain.
    """

    async def snapshot(self) -> ActiveWork:
        from jobs.busy import open_run_rows

        rows = [
            row for row in open_run_rows() if row.get("job") == "bus-reply-watch"
        ]
        return ActiveWork(busy=bool(rows), detail={"open": rows})


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
        # Same payload as GIW. ``busy`` is admission ``active_count``
        # (tickets ∪ live dispatches ∪ claimed Auto jobs), not the
        # running/queued/cursor_dispatches/write_lease fields. The propagate
        # caller's own job is dropped in ``evaluate`` via ``exclude_job_id``;
        # this probe does not change GIW's busy flag.
        "agent_bus": HttpActiveWorkProbe(
            GIT_INTEGRATION_WORKER_URL, "/api/v1/git/active-work"
        ),
        "mcp": build_mcp_busy_probe(),
        "jobs": JobsBusyProbe(),
    }
    cfg = cdp_ask_url_config()
    if cfg is not None:
        _host, _port, base = cfg
        probes["cdp_ask"] = HttpActiveWorkProbe(base, "/v1/project-ask/drain-state")
    return probes


def default_restart_witnesses() -> dict[str, list[InFlightWitness]]:
    """Service → in-flight witnesses consulted when the live probe fails.

    cdp_ask's witness reads the registry ``execution_state`` rows — the same
    durable authority its ``/drain-state`` busy flag derives from — so a
    pinned or already-dead process still yields a named busy or a real idle.
    """
    from .cdp_ask_inflight_witness import CdpAskInFlightWitness

    witnesses: dict[str, list[InFlightWitness]] = {}
    if cdp_ask_url_config() is not None:
        witnesses["cdp_ask"] = [CdpAskInFlightWitness()]
    return witnesses


class RestartDrainGate:
    """Per-service restart mutex + busy-probe drain check.

    One instance is owned by ServiceController so the per-service gates persist
    across manage calls (coalescing requires shared state).

    ``witnesses`` are per-service in-flight sources consulted only when the
    live probe raises (``restart_drain_witness``). Every service additionally
    gets the gate's own ``LastProbeWitness`` — the last answered probe.
    """

    def __init__(
        self,
        probes: dict[str, BusyProbe] | None = None,
        *,
        witnesses: dict[str, list[InFlightWitness]] | None = None,
    ) -> None:
        self._probes: dict[str, BusyProbe] = (
            probes if probes is not None else _default_probes()
        )
        self._gates: dict[str, FifoCapacityGate] = {}
        self._witnesses: dict[str, list[InFlightWitness]] = witnesses or {}
        self._last_probe = LastProbeWitness()

    def _witnesses_for(self, service: str) -> list[InFlightWitness]:
        return [*self._witnesses.get(service, ()), self._last_probe]

    def _gate(self, service: str) -> FifoCapacityGate:
        gate = self._gates.get(service)
        if gate is None:
            gate = FifoCapacityGate(limit=1, gate_id=f"restart:{service}")
            self._gates[service] = gate
        return gate

    def _probe(self, service: str) -> BusyProbe:
        return self._probes.get(service, NullBusyProbe())

    async def evaluate(
        self,
        service: str,
        *,
        force: bool,
        supervised_drain: bool = False,
        exclude_job_id: str | None = None,
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
                # Probe failure must not kill a maybe-busy service. Fail closed,
                # but ask the witnesses first: a recent busy answer names the
                # work; only an independent idle witness may clear the restart.
                detail = describe_probe_exc(exc)
                logger.warning("active-work probe failed for %s: %s", service, detail)
                resolution = await resolve_probe_failure(
                    service, probe_error=detail, witnesses=self._witnesses_for(service)
                )
                if resolution.verdict == "idle":
                    logger.info("restart of %s: %s", service, resolution.reason)
                    proceed = True
                    return None  # slot held; proceed
                return DrainOutcome(
                    state=resolution.state,
                    service=service,
                    reason=resolution.reason,
                    active_work=resolution.active_work,
                )

            if (
                service == "agent_bus"
                and isinstance(exclude_job_id, str)
                and exclude_job_id.strip()
            ):
                work = exclude_caller_from_agent_bus_census(
                    work.detail, exclude_job_id=exclude_job_id
                )

            if work.busy:
                return DrainOutcome(
                    state="busy",
                    service=service,
                    reason=in_flight_defer_reason(work.detail),
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
        Every answered probe is recorded for ``LastProbeWitness``.
        """
        work = await self._probe(service).snapshot()
        self._last_probe.record(service, busy=work.busy, detail=work.detail)
        return work

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

        For each service, returns ``busy``, ``restart_would_defer``,
        ``determination``, and ``active_work``.

        ``restart_would_defer`` ⟺ ``busy`` ∨ a restart is already in progress ∨
        the probe failed with no witness verdict. Probe failure goes through
        the same witnesses as ``evaluate``: a busy witness reports ``busy``;
        otherwise ``busy=False``, ``determination=undetermined``,
        ``restart_would_defer=True`` (fail closed: a non-force restart would
        defer with ``state=probe_error``) and an ``error`` entry in
        ``active_work``. ``busy=False`` alone is not an idle finding — read
        ``determination``.
        """
        report: dict[str, dict[str, Any]] = {}
        for service in services:
            in_progress = self.restart_in_progress(service)
            try:
                work = await self.probe(service)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                resolution = await resolve_probe_failure(
                    service,
                    probe_error=describe_probe_exc(exc),
                    witnesses=self._witnesses_for(service),
                )
                report[service] = resolution.busy_report_row(
                    restart_in_progress=in_progress
                )
                continue
            from services.git_integration_worker.drain_progress import (
                reclassify_dead_heartbeat_work,
            )

            busy, detail = reclassify_dead_heartbeat_work(
                work.detail, busy=work.busy
            )
            if busy:
                determination = "busy"
            elif detail.get("determination") == "dead_heartbeat":
                determination = "dead_heartbeat"
            elif in_progress:
                determination = "in_progress"
            else:
                determination = "idle"
            report[service] = {
                "busy": busy,
                "restart_would_defer": busy or in_progress,
                "determination": determination,
                "active_work": detail,
            }
        return report


async def run_gated(
    gate: RestartDrainGate,
    action: str,
    service: str,
    *,
    force: bool,
    lifecycle: Callable[[], Awaitable[str]],
    exclude_job_id: str | None = None,
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
    outcome = await gate.evaluate(service, force=force, exclude_job_id=exclude_job_id)
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

        try:
            await supervisor.supervise(intent)
        finally:
            await release_once()

    task = asyncio.create_task(_background())
    _SUPERVISE_TASKS.add(task)
    task.add_done_callback(_SUPERVISE_TASKS.discard)
    from .drain_timeout_keep_await import register_supervise_task

    register_supervise_task(service, task)


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
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.deadline_s
        intent_id = intent.intent_id
        try:
            while True:
                if loop.time() >= deadline:
                    # Alert ceiling only — do not hide the intent from busy_status.
                    deadline = loop.time() + self.deadline_s
                work = await self.gate.probe(self.service)
                if not work.busy:
                    break
                await asyncio.sleep(self.poll_interval_s)
            self.store.advance(intent_id, status=STATUS_DRAINED_RESTARTING)
            await self.lifecycle()
            self.store.advance(intent_id, status=STATUS_COMPLETED)
        except Exception:
            current = self.store.get(intent_id)
            if current is not None and current.status != STATUS_COMPLETED:
                self.store.advance(intent_id, status=STATUS_FAILED)
            raise


def stargate_idle_from_active_work(detail: dict[str, Any]) -> bool:
    """True when Stargate active-work reports no in-flight work."""
    if not detail:
        return True
    if "busy" in detail:
        return not bool(detail.get("busy"))
    total = detail.get("total")
    if isinstance(total, int):
        return total <= 0
    in_flight = detail.get("requests_in_flight")
    async_running = detail.get("async_pipelines_running")
    if isinstance(in_flight, int) and isinstance(async_running, int):
        return in_flight + async_running <= 0
    return not bool(detail.get("busy", True))


@dataclass(slots=True, kw_only=True)
class StargateIdleDrainSupervisor:
    """Wait for Stargate idle (active-work probe), then lifecycle; ceiling force."""

    gate: RestartDrainGate
    store: Any
    lifecycle: Callable[[], Awaitable[str]]
    deadline_s: float = STARGATE_OPERATOR_IDLE_CEILING_S
    poll_interval_s: float = _SELF_HOLDER_POLL_INTERVAL_S

    async def supervise(self, intent: Any) -> None:
        from .restart_intent_states import (
            STATUS_COMPLETED,
            STATUS_DRAINED_RESTARTING,
            STATUS_FAILED,
            STATUS_FORCE_REQUESTED,
        )

        loop = asyncio.get_running_loop()
        ceiling = loop.time() + self.deadline_s
        intent_id = intent.intent_id
        escalated = False
        try:
            while True:
                work = await self.gate.probe("stargate")
                if stargate_idle_from_active_work(work.detail):
                    break
                if loop.time() >= ceiling:
                    escalated = True
                    self.store.advance(intent_id, status=STATUS_FORCE_REQUESTED)
                    break
                await asyncio.sleep(self.poll_interval_s)
            self.store.advance(intent_id, status=STATUS_DRAINED_RESTARTING)
            await self.lifecycle()
            self.store.advance(intent_id, status=STATUS_COMPLETED)
            if escalated:
                logger.info(
                    "stargate drain supervisor completed after idle-ceiling force "
                    "(intent_id=%s)",
                    intent_id,
                )
        except Exception:
            current = self.store.get(intent_id)
            if current is not None and current.status != STATUS_COMPLETED:
                self.store.advance(intent_id, status=STATUS_FAILED)
            raise


def cdp_legs_orphaned_from_active_work(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Operator-proxy and mission legs a restart will orphan.

    Empty when the probe did not name any. Callers put this list on both
    the deferred envelope and the armed receipt so the drain is not silent.
    """
    raw = detail.get("cdp_legs_orphaned")
    if not isinstance(raw, list):
        return []
    named: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        execution_id = str(item.get("execution_id") or "").strip()
        purpose = str(item.get("purpose") or "").strip()
        if execution_id and purpose:
            named.append({"execution_id": execution_id, "purpose": purpose})
    return named


async def run_gated_stargate_idle_drain_supervised(
    gate: RestartDrainGate,
    action: str,
    *,
    store: Any,
    supervisor: StargateIdleDrainSupervisor,
    reason: str,
    code_ref: str = "HEAD",
    row_id: str | None = None,
    caller_agent: str | None = None,
) -> dict[str, Any]:
    """Arm a durable restart intent when Stargate active-work reports busy."""
    orphaned: list[dict[str, Any]] = []
    try:
        probed = await gate.probe("stargate")
        orphaned = cdp_legs_orphaned_from_active_work(probed.detail)
    except (httpx.HTTPError, ValueError, OSError):
        orphaned = []
    outcome = await gate.evaluate("stargate", force=True, supervised_drain=True)
    if outcome is not None:
        existing = store.active_for_service("stargate")
        if existing is not None:
            validation_id = mint_activation_validation(
                store, existing, code_ref=code_ref, row_id=row_id
            )
            deferred = drain_deferred_result(
                existing,
                reason="drain already in progress for this service",
                activation_validation_id=validation_id,
            )
            deferred["cdp_legs_orphaned"] = orphaned
            return deferred
        proceeded = outcome.to_result()
        proceeded["cdp_legs_orphaned"] = orphaned
        return proceeded

    deadline_at = (
        datetime.now(UTC) + timedelta(seconds=supervisor.deadline_s)
    ).isoformat()
    try:
        intent = store.create_intent(
            service="stargate",
            action=action,
            deadline_at=deadline_at,
            reason=reason,
            caller_agent=caller_agent,
        )
        validation_id = mint_activation_validation(
            store, intent, code_ref=code_ref, row_id=row_id
        )
    except Exception:
        await gate.release("stargate")
        raise
    await open_service_window(store, "stargate", reason=f"stargate idle drain {action}")
    _spawn_supervised(gate, "stargate", supervisor, intent)
    result = drain_deferred_result(intent, activation_validation_id=validation_id)
    result["cdp_legs_orphaned"] = orphaned
    result["idle_ceiling_s"] = supervisor.deadline_s
    result["guidance"] = (
        "Stargate restart intent armed — fires when active-work reports idle, "
        f"or self-preempts at {int(supervisor.deadline_s)}s ceiling. "
        "Query restart_intent_status or fleet_liveness after converge."
    )
    return result


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
    caller_agent: str | None = None,
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
            caller_agent=caller_agent,
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
    wait_for_boundary: bool = False,
    intent_ttl_s: float | None = None,
    caller_agent: str | None = None,
) -> dict[str, Any]:
    """Arm a durable git-worker drain intent and return the deferred 202 envelope.

    ``park_live`` (steer-restart v1) makes the supervisor park live cursor-sdk
    dispatches at drain start instead of keep-awaiting them.
    ``wait_for_boundary`` (a:37197) defers begin_drain until GIW idle so admits
    stay open during a long occupant row; optional ``intent_ttl_s`` bounds the arm.
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
            wait_for_boundary=wait_for_boundary,
            intent_ttl_s=intent_ttl_s,
            caller_agent=caller_agent,
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
    caller_agent: str | None = None,
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
            service=service,
            action=action,
            deadline_at=deadline_at,
            reason=reason,
            caller_agent=caller_agent,
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
    "STARGATE_OPERATOR_IDLE_CEILING_S",
    "StargateIdleDrainSupervisor",
    "ActiveWork",
    "BackgroundCompleteHook",
    "BackgroundFailedHook",
    "BusyProbe",
    "DrainOutcome",
    "GATED_ACTIONS",
    "GIT_INTEGRATION_WORKER_URL",
    "HttpActiveWorkProbe",
    "InFlightWitness",
    "LastProbeWitness",
    "default_restart_witnesses",
    "LocalServiceDrainSupervisor",
    "NullBusyProbe",
    "RETRY_AFTER_S",
    "RestartDrainGate",
    "STARGATE_PROBE_URL",
    "agent_bus_restart_would_defer",
    "describe_probe_exc",
    "exclude_caller_from_agent_bus_census",
    "holder_dispatch_id_from_active_work",
    "in_flight_defer_reason",
    "resume_drain_supervision",
    "run_gated",
    "run_gated_deferred",
    "run_gated_drain_supervised",
    "run_gated_drain_supervised_blocking",
    "run_gated_self_holder_drain_supervised",
    "run_gated_stargate_idle_drain_supervised",
    "sole_busy_holder_matches",
    "cdp_legs_orphaned_from_active_work",
    "stargate_idle_from_active_work",
]
