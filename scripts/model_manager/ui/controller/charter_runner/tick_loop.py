"""Manage-hosted charter-runner tick.

Periodic supervisor that watches enrolled standing roots and admits one window
per eligible root. Default substrate is unattended Grok 4.5
(``cursor/grok-4.5``, effort=high, fast=true) via generate dispatch.
Per-root todo ``attendance=autonomous`` selects the background-lead packet and
auto-arms hard stall at ``DEFAULT_AUTONOMOUS_STALE_S`` (3600s) unless
``CHARTER_UNATTENDED_STALE_S`` overrides (incl. ``0`` = force OFF).
Consult-mode windows additionally recover at ``DEFAULT_CONSULT_STALE_S`` (900s)
via ``consult_stall`` (R-ADMIT on root advances; else re-queue CONSULT_PENDING)
so a hung CDP/consult seat cannot pin the root for a full hour (a:26131).
CHECKPOINT on the charter root clears in-flight.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
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

from . import bus_client
from . import state_close as _state_close_mod
from .attendance import Attendance, admission_mode_for_attendance
from .caps import CapStore
from .dispatch_client import AdmissionMode
from .env_snapshot import EnvSnapshot
from .giw_live_hold import build_tick_env_snapshot
from .harvest import completed_windows, harvest_completed_windows
from .tick_interval import tick_interval_from_env as _tick_interval_from_env

MAX_STATE_CLOSES_PER_TICK = _state_close_mod.MAX_STATE_CLOSES_PER_TICK
emit_skip_and_maybe_state_close = _state_close_mod.emit_skip_and_maybe_state_close
maybe_state_close_root = _state_close_mod.maybe_state_close_root
# Consult stall recover retired at Phase 3 — constant retained for test imports.
DEFAULT_CONSULT_STALE_S = 900.0

logger = get_logger(__name__)


_ACTIVITY = "charter_tick"
# Soft remind only — attended handoffs may sit until the operator opens IDE.
_WAITING_OPEN_REMIND_S = 900.0
# Hard stall (stale_window): autonomous default-on; other modes env-opt-in.
_ENV_UNATTENDED_STALE_S = "CHARTER_UNATTENDED_STALE_S"
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


def _env_unattended_stale_raw() -> float | None:
    """Parse CHARTER_UNATTENDED_STALE_S; None means unset or malformed (A5).

    Empty/whitespace → None (unset). Non-float → None (malformed→unset).
    Negatives clamp to 0.0 (force-OFF). Explicit 0.0 is force-OFF, not unset.
    """
    raw = os.environ.get(_ENV_UNATTENDED_STALE_S, "").strip()
    if not raw:
        return None  # unset
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # malformed → treat as unset (A5)


def _unattended_stale_s_from_env() -> float:
    """Legacy shim: unset/malformed → 0.0; else parsed seconds. Delegates to raw."""
    val = _env_unattended_stale_raw()
    return 0.0 if val is None else val


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


def _admission_mode_from_env(env: EnvSnapshot, root_id: str) -> AdmissionMode:
    """Per-root admission mode from tick-scoped env snapshot."""
    return admission_mode_for_attendance(_attendance_for_root(env, root_id))


def _effective_unattended_stale_s(
    *,
    constructor_override: float | None,
    root_id: str | None = None,
    env: EnvSnapshot | None = None,
) -> float:
    """Resolve hard-stall seconds: constructor → env → per-root autonomous default → 0.

    When ``root_id`` carries todo ``attendance=autonomous`` and env is unset,
    returns DEFAULT_AUTONOMOUS_STALE_S (3600). Explicit env always wins.
    """
    if constructor_override is not None:
        return max(0.0, float(constructor_override))
    env_val = _env_unattended_stale_raw()
    if env_val is not None:
        return env_val
    if root_id is not None and env is not None:
        if _admission_mode_from_env(env, root_id) == "autonomous":
            return DEFAULT_AUTONOMOUS_STALE_S
    return 0.0


def ensure_charter_tick_env(workspace_root: Path) -> None:
    """Overlay bus auth into the manage process env (token is not ambient)."""
    merged = build_mcp_env(workspace_root)
    for key in _ENV_KEYS:
        if merged.get(key):
            os.environ[key] = merged[key]


class CharterRunnerTickLoop:
    """Async supervisor: scan enrolled roots + admit generate or handoff windows."""

    def __init__(
        self,
        *,
        service_state: ServiceState,
        shutdown_gate: ManageShutdownGate,
        workspace_root: Path | None = None,
        tick_interval_s: float | None = None,
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
        # None / omitted → env (CHARTER_TICK_INTERVAL_S); explicit float wins (tests).
        if tick_interval_s is None:
            self._tick_interval_s = _tick_interval_from_env()
        else:
            self._tick_interval_s = float(tick_interval_s)
        self._caps = caps or CapStore()
        self._on_admit = on_admit
        # None → resolve at handle time (env / autonomous default); float freezes tests.
        self._unattended_stale_override = unattended_stale_s
        self._loop_task: asyncio.Task[None] | None = None
        self._reminded: set[str] = set()

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        if self._workspace_root is not None:
            ensure_charter_tick_env(self._workspace_root)
        from .propagation_execute import install_propagation_context

        install_propagation_context(
            self._service_controller,
            event_bus=self._event_bus,
        )
        self._loop_task = asyncio.create_task(self._run_loop())
        await events.emit_manage_charter_tick_started()

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
        from .propagation_execute import install_propagation_context

        install_propagation_context(None)
        await events.emit_manage_charter_tick_stopped()

    async def _run_loop(self) -> None:
        try:
            while True:
                if not self._services_healthy():
                    await asyncio.sleep(self._tick_interval_s)
                    continue
                self._shutdown_gate.set_activity(_ACTIVITY, True)
                try:
                    await self._tick_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — tick errors are non-fatal
                    logger.exception("charter tick failed")
                    await events.emit_manage_charter_tick_error(reason=str(exc))
                finally:
                    self._shutdown_gate.set_activity(_ACTIVITY, False)
                await asyncio.sleep(self._tick_interval_s)
        except asyncio.CancelledError:
            self._shutdown_gate.set_activity(_ACTIVITY, False)
            raise

    def _services_healthy(self) -> bool:
        cortex = self._service_state.check_cortex_api()
        if cortex.status != ServiceStatus.RUNNING:
            return False
        bus = self._service_state.check_agent_bus()
        return bus.status == ServiceStatus.RUNNING

    async def _tick_once(self) -> None:
        """Phase 3: kernel is sole admitter for all enrolled roots."""
        from .enrollment_filter import refresh_migrated_roots_cache
        from .env_snapshot import build_env_snapshot
        from .kernel_tick import apply_kernel_tick_for_root

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
            if kernel_outcome.skipped_reason:
                skipped_by_reason[kernel_outcome.skipped_reason] = (
                    skipped_by_reason.get(kernel_outcome.skipped_reason, 0) + 1
                )
            elif label == "kernel_unseeded":
                skipped_by_reason["kernel_unseeded"] = (
                    skipped_by_reason.get("kernel_unseeded", 0) + 1
                )
                await events.emit_manage_charter_tick_root_skipped(
                    root=root_id,
                    reason="kernel_unseeded",
                    checkpoint_turn=None,
                )
        try:
            from .kernel import record_shadow_pass
            from .telemetry import emit_shadow_diff, emit_shadow_ledger_starved

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
            from . import tick_friction_reconcile as _tick_reconcile

            await _tick_reconcile.reconcile_enrolled_roots_on_tick(roots)
        except Exception:  # noqa: BLE001 — reconcile must not abort tick
            logger.exception("charter-runner tick-scan friction reconcile failed")
        try:
            from . import conveyor as _conveyor_mod

            await _conveyor_mod.sweep_stale_enrollments()
        except Exception:  # noqa: BLE001 — stale sweep must not abort tick
            logger.exception("charter-runner conveyor stale sweep failed")


def _admission_posted_at(admission_turn: dict) -> datetime | None:
    try:
        meta = json.loads(str(admission_turn.get("body") or ""))
        raw = meta.get("posted_at")
        if not raw:
            return None
        parsed = datetime.fromisoformat(raw)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None
