"""Manage-hosted charter runner host.

Periodic supervisor that watches enrollments on the roster and launches one run
per eligible enrollment. Default substrate is unattended Grok 4.5
(``cursor/grok-4.5``, effort=high, fast=true) via generate dispatch.
Per-root todo ``attendance=autonomous`` selects the background-lead packet and
auto-arms hard stall at ``DEFAULT_AUTONOMOUS_STALE_S`` (3600s) unless
``CHARTER_UNATTENDED_STALE_S`` overrides (incl. ``0`` = force OFF).
Consult-mode windows additionally recover at ``DEFAULT_CONSULT_STALE_S`` (900s)
via ``consult_stall`` (R-ADMIT on root advances; else re-queue CONSULT_PENDING)
so a hung CDP/consult seat cannot pin the root for a full hour (a:26131).
CHECKPOINT on the enrollment bus clears in-flight runs.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

from scripts.model_manager import observation_event as events
from scripts.model_manager.ui.controller.service_config import build_mcp_env
from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
from scripts.model_manager.ui.model.service_state import ServiceState, ServiceStatus

if TYPE_CHECKING:
    from universal_event_bus import EventBus

    from scripts.model_manager.ui.controller.service_ctl.core import ServiceController

from .. import bus_client
from .. import state_close as _state_close_mod
from ..admission import CapStore
from ..attendance import Attendance, admission_mode_for_attendance
from ..dispatch_client import AdmissionMode
from ..env_snapshot import EnvSnapshot
from ..giw_live_hold import build_tick_env_snapshot
from ..harvest import completed_windows, harvest_completed_windows
from .interval import (
    reconcile_interval_from_env as _reconcile_interval_from_env,
)
from .interval import (
    tick_interval_from_env as _tick_interval_from_env,
)

MAX_STATE_CLOSES_PER_TICK = _state_close_mod.MAX_STATE_CLOSES_PER_TICK
emit_skip_and_maybe_state_close = _state_close_mod.emit_skip_and_maybe_state_close
maybe_state_close_root = _state_close_mod.maybe_state_close_root
# Consult stall recover retired at Phase 3 — constant retained for test imports.
DEFAULT_CONSULT_STALE_S = 900.0

logger = get_logger(__name__)


_ACTIVITY = "charter_tick"
_ENV_KEYS = ("AGENT_BUS_TOKEN",)
# Margin over CURSOR_SDK_TIMEOUT (1800) so stale cannot race worker_failed (A1).
DEFAULT_AUTONOMOUS_STALE_S = 3600.0

# Re-export for tests that import via tick_loop.
_completed_windows = completed_windows


async def maybe_heal_admit_intent_orphan(
    root_id: str,
    turns: list[dict],
    caps: CapStore,
) -> bool:
    """Phase 3: heal retired — always no-op (kept for test import stability)."""
    _ = (root_id, turns, caps)
    return False


def _attendance_for_root(env: EnvSnapshot, root_id: str) -> Attendance:
    """Attendance from tick-scoped env snapshot; warn and default attended when missing."""
    attendance = env.attendance_by_root.get(root_id)
    if attendance is None:
        logger.warning(
            "attendance missing from env snapshot root_id=%s — defaulting attended",
            root_id,
        )
        return "attended"
    if attendance == "autonomous":
        return "autonomous"
    if attendance == "operator_proxy":
        return "operator_proxy"
    return "attended"


def _arc_lane_from_env(env: EnvSnapshot, root_id: str) -> str:
    """Per-root arc lane from tick-scoped env snapshot; default layer."""
    arc_lane = env.arc_lane_by_root.get(root_id)
    if arc_lane is None:
        logger.warning(
            "arc_lane missing from env snapshot root_id=%s — defaulting layer",
            root_id,
        )
        return "layer"
    return arc_lane


def _admission_mode_from_env(env: EnvSnapshot, root_id: str) -> AdmissionMode:
    """Per-root admission mode from tick-scoped env snapshot."""
    return admission_mode_for_attendance(_attendance_for_root(env, root_id))


def ensure_charter_tick_env(workspace_root: Path) -> None:
    """Overlay bus auth into the manage process env (token is not ambient)."""
    merged = build_mcp_env(workspace_root)
    for key in _ENV_KEYS:
        if merged.get(key):
            os.environ[key] = merged[key]


class CharterRunnerTickLoop:
    """Async supervisor: scan roster + launch generate or handoff runs."""

    def __init__(
        self,
        *,
        service_state: ServiceState,
        shutdown_gate: ManageShutdownGate,
        workspace_root: Path | None = None,
        tick_interval_s: float | None = None,
        reconcile_interval_s: float | None = None,
        caps: CapStore | None = None,
        on_admit: Callable[[str], None] | None = None,
        unattended_stale_s: float | None = None,
        service_controller: ServiceController | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._service_state = service_state
        self._shutdown_gate = shutdown_gate
        self._workspace_root = workspace_root
        self._service_controller = service_controller
        self._event_bus = event_bus
        # Explicit float wins (tests). Floor default: CHARTER_RECONCILE_INTERVAL_S (300s).
        if reconcile_interval_s is not None:
            self._reconcile_interval_s = float(reconcile_interval_s)
        elif tick_interval_s is not None:
            self._reconcile_interval_s = float(tick_interval_s)
        else:
            self._reconcile_interval_s = _reconcile_interval_from_env()
        if tick_interval_s is None:
            self._tick_interval_s = _tick_interval_from_env()
        else:
            self._tick_interval_s = float(tick_interval_s)
        self._caps = caps or CapStore()
        self._on_admit = on_admit
        # Retained for test kwargs; Phase-3 kernel owns stall recovery.
        self._unattended_stale_override = unattended_stale_s
        self._loop_task: asyncio.Task[None] | None = None
        self._held_heartbeat_at: float = 0.0
        self._tick_counter: int = 0
        self._wake_hub = None
        self._wake_consumer = None

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        if self._workspace_root is not None:
            ensure_charter_tick_env(self._workspace_root)
        from ..propagation_execute import install_propagation_context
        from ..wake_consumer import WakeConsumer
        from ..wake_hub import (
            WakeDirtySet,
            WakeHub,
            WakeRootMapper,
            build_wake_subscribe_factory,
        )
        from . import hold as tick_hold

        install_propagation_context(
            self._service_controller,
            event_bus=self._event_bus,
        )
        dirty = WakeDirtySet()
        mapper = WakeRootMapper(bus_client.list_enrolled_roots)

        async def _on_wake(root_id: str, signal: str, coalesced_n: int) -> None:
            from scripts.model_manager import (
                observation_event_charter as charter_events,
            )

            await charter_events.emit_manage_charter_tick_wake(
                root=root_id,
                signal=signal,
                coalesced_n=coalesced_n,
            )

        async def _on_full_roster_wake() -> None:
            if self._wake_consumer is not None:
                await self._wake_consumer.enqueue_full_roster()

        self._wake_hub = WakeHub(
            dirty=dirty,
            mapper=mapper,
            caps=self._caps,
            subscribe_events=build_wake_subscribe_factory(),
            on_wake=_on_wake,
            on_full_roster_wake=_on_full_roster_wake,
        )
        self._wake_consumer = WakeConsumer(
            tick_loop=self,
            dirty=dirty,
            mapper=mapper,
            floor_interval_s=self._reconcile_interval_s,
            services_healthy=self._services_healthy,
            _shutdown_gate_activity=lambda active: self._shutdown_gate.set_activity(
                _ACTIVITY, active
            ),
        )
        await self._wake_hub.start()
        await self._wake_consumer.start()
        self._loop_task = asyncio.create_task(self._run_loop())
        await events.emit_manage_charter_tick_started()
        held = tick_hold.read_hold()
        if held is not None:
            self._held_heartbeat_at = await tick_hold.emit_held_if_due(
                held, last_emitted_at=self._held_heartbeat_at, force=True
            )

    async def stop(self) -> None:
        loop_task = self._loop_task
        if loop_task is None:
            return
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
        self._loop_task = None
        if self._wake_consumer is not None:
            await self._wake_consumer.stop()
            self._wake_consumer = None
        if self._wake_hub is not None:
            await self._wake_hub.stop()
            self._wake_hub = None
        from ..propagation_execute import install_propagation_context

        install_propagation_context(None)
        await events.emit_manage_charter_tick_stopped()

    async def _run_loop(self) -> None:
        """Hold heartbeat loop — wake consumer owns floor + dirty passes."""
        from . import hold as tick_hold

        try:
            while True:
                held = tick_hold.read_hold()
                if held is not None:
                    self._held_heartbeat_at = await tick_hold.emit_held_if_due(
                        held, last_emitted_at=self._held_heartbeat_at
                    )
                await asyncio.sleep(self._reconcile_interval_s)
        except asyncio.CancelledError:
            raise

    def _services_healthy(self) -> bool:
        cortex = self._service_state.check_cortex_api()
        if cortex.status != ServiceStatus.RUNNING:
            return False
        bus = self._service_state.check_agent_bus()
        return bus.status == ServiceStatus.RUNNING

    async def _tick_once(self) -> None:
        """Phase 3: kernel is sole launcher for all enrollments."""
        from ..enrollment_filter import refresh_migrated_roots_cache
        from ..env_snapshot import build_env_snapshot
        from ..kernel_tick import apply_kernel_tick_for_root

        refresh_migrated_roots_cache()
        roots = await bus_client.list_enrolled_roots()
        env_snapshot = await build_tick_env_snapshot()
        root_ids = [str(thread.get("id") or "") for thread in roots if thread.get("id")]
        kernel_env = await build_env_snapshot(
            root_ids=root_ids,
            env_half=env_snapshot,
        )
        admitted = 0
        in_flight = 0
        state_closes_this_tick = 0
        skipped_by_reason: dict[str, int] = {}
        closed_attributions: list[str] = []
        old_decisions: dict[str, str] = {}
        for thread in roots:
            root_id = str(thread.get("id") or "")
            if not root_id:
                continue
            turns = await bus_client.fetch_turns(root_id)
            closed_attributions.extend(
                await harvest_completed_windows(
                    root_id,
                    turns,
                    admission_mode=_admission_mode_from_env(kernel_env, root_id),
                )
            )
            kernel_outcome = await apply_kernel_tick_for_root(
                root_id,
                turns,
                caps=self._caps,
                workspace_root=self._workspace_root,
                env=kernel_env,
                on_admit=self._on_admit,
                admission_mode=_admission_mode_from_env(kernel_env, root_id),
            )
            old_decisions[root_id] = kernel_outcome.old_decision_label
            if kernel_outcome.admitted:
                admitted += 1
            label = kernel_outcome.old_decision_label
            if label == "NOOP":
                in_flight += 1
            from .skip_side_effects import apply_skip_side_effects

            try:
                state_closes_this_tick = await apply_skip_side_effects(
                    root_id=root_id,
                    turns=turns,
                    skipped_reason=kernel_outcome.skipped_reason,
                    old_decision_label=label,
                    admitted=kernel_outcome.admitted,
                    state_closes_this_tick=state_closes_this_tick,
                    skipped_by_reason=skipped_by_reason,
                    caps=self._caps,
                    fire_attempt_outcome=kernel_outcome.fire_attempt_outcome,
                    fire_attempt_reason=kernel_outcome.fire_attempt_reason,
                )
            except Exception:  # noqa: BLE001 — skip/SOS must not abort tick
                logger.exception("charter-runner skip side-effects failed")
        try:
            from ..telemetry import emit_shadow_diff, emit_shadow_ledger_starved
            from .shadow import record_shadow_pass

            shadow = record_shadow_pass(old_decisions, env=kernel_env)
            if shadow.starved:
                await emit_shadow_ledger_starved(
                    reason=shadow.starve_reason or "ledger_empty",
                    bus_roots=shadow.bus_roots,
                )
            for row in shadow.rows:
                if row.get("starved"):
                    continue
                await emit_shadow_diff(
                    root=row["root"],
                    old_decision=row["old_decision"],
                    kernel_transition=row["kernel_transition"],
                    classification=row["classification"],
                )
        except Exception:  # noqa: BLE001 — shadow must not abort tick
            logger.exception("charter-runner shadow pass failed")
        self._tick_counter += 1
        try:
            if self._service_controller is not None:
                from ..propagation_harvest_wanted import consume_harvest_wanted_at_tick

                hw_results = await consume_harvest_wanted_at_tick(
                    tick_index=self._tick_counter,
                    service_controller=self._service_controller,
                    event_bus=self._event_bus,
                )
                if hw_results.get("closed") or hw_results.get("failed"):
                    await events.emit_manage_charter_tick_harvest_wanted_consumed(
                        tick_index=self._tick_counter,
                        results=hw_results,
                    )
        except Exception:  # noqa: BLE001 — harvest_wanted must not abort tick
            logger.exception("charter-runner harvest_wanted consumption failed")
        await events.emit_manage_charter_tick_scanned(
            roots=len(roots),
            admitted=admitted,
            skipped_by_reason=skipped_by_reason,
        )
        try:
            from pager_notify import notify_tick_complete, scan_operator_bus_turns

            await notify_tick_complete(
                roots=len(roots),
                in_flight=in_flight,
                admitted=admitted,
                skipped_by_reason=skipped_by_reason,
                closed_attributions=closed_attributions or None,
            )
            await scan_operator_bus_turns()
        except Exception:  # noqa: BLE001 — pager must not abort tick
            logger.exception("charter-runner pager notify failed")
        try:
            from .. import tick_friction_reconcile as _tick_reconcile

            await _tick_reconcile.reconcile_enrolled_roots_on_tick(roots)
        except Exception:  # noqa: BLE001 — reconcile must not abort tick
            logger.exception("charter-runner tick-scan friction reconcile failed")
