"""Manage-hosted charter-runner tick.

Periodic supervisor that watches enrolled standing roots and admits one window
per eligible root. Default substrate is unattended Grok 4.5 High
(``cursor/grok-4.5``, effort=high, fast=false) via generate dispatch.
Opt-in attended handoff (``CHARTER_ADMISSION_MODE=handoff``) POSTs
``/api/v1/team/handoff`` with ``role=cursor-consult`` for IDE observation.
CHECKPOINT on the charter root clears in-flight.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from universal_logging import get_logger

from scripts.model_manager import observation_event as events
from scripts.model_manager.ui.controller.service_config import build_mcp_env
from scripts.model_manager.ui.controller.shutdown_gate import ManageShutdownGate
from scripts.model_manager.ui.model.service_state import ServiceState, ServiceStatus

from . import bus_client, dispatch_client, window_log
from .caps import CapStore
from .dispatch_client import AdmissionMode
from .eligibility import (
    ADMISSION_SUBJECT_PREFIX,
    Decision,
    evaluate_root,
)
from .harvest import completed_windows, harvest_completed_windows
from .materializer import handoff_subject, materialize_resume_packet

logger = get_logger(__name__)

_DEFAULT_TICK_INTERVAL_S = 60.0
_ACTIVITY = "charter_tick"
# Soft remind only — attended handoffs may sit until the operator opens IDE.
_WAITING_OPEN_REMIND_S = 900.0
# Hard stall (stale_window) is opt-in / unattended only — 0 or unset = OFF.
_ENV_UNATTENDED_STALE_S = "CHARTER_UNATTENDED_STALE_S"
_ENV_ADMISSION_MODE = "CHARTER_ADMISSION_MODE"
_ENV_KEYS = ("AGENT_BUS_TOKEN",)

# Re-export for tests that import via tick_loop.
_completed_windows = completed_windows


def _unattended_stale_s_from_env() -> float:
    """Return hard-stall seconds when armed; 0 disables (attended default)."""
    raw = os.environ.get(_ENV_UNATTENDED_STALE_S, "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _admission_mode() -> AdmissionMode:
    """Read ``CHARTER_ADMISSION_MODE``; default generate; unknown → generate + warn."""
    raw = os.environ.get(_ENV_ADMISSION_MODE, "").strip().lower()
    if not raw or raw == "generate":
        return "generate"
    if raw == "handoff":
        return "handoff"
    logger.warning(
        "charter-runner: unknown CHARTER_ADMISSION_MODE=%r — treating as generate",
        raw,
    )
    return "generate"


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
        tick_interval_s: float = _DEFAULT_TICK_INTERVAL_S,
        caps: CapStore | None = None,
        on_admit: Callable[[str], None] | None = None,
        unattended_stale_s: float | None = None,
    ) -> None:
        self._service_state = service_state
        self._shutdown_gate = shutdown_gate
        self._workspace_root = workspace_root
        self._tick_interval_s = tick_interval_s
        self._caps = caps or CapStore()
        self._on_admit = on_admit
        # None → env; 0 / unset env → hard-fail OFF (preserve attended sit-open).
        self._unattended_stale_s = (
            _unattended_stale_s_from_env()
            if unattended_stale_s is None
            else max(0.0, float(unattended_stale_s))
        )
        self._loop_task: asyncio.Task[None] | None = None
        self._reminded: set[str] = set()

    async def start(self) -> None:
        if self._loop_task is not None:
            return
        if self._workspace_root is not None:
            ensure_charter_tick_env(self._workspace_root)
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
        roots = await bus_client.list_enrolled_roots()
        admitted = 0
        for thread in roots:
            root_id = str(thread.get("id") or "")
            if not root_id:
                continue
            turns = await bus_client.fetch_turns(root_id)
            await harvest_completed_windows(root_id, turns)
            decision = evaluate_root(root_id, turns, self._caps)
            if decision.eligible:
                if await self._admit_window(decision, turns):
                    admitted += 1
            elif decision.reason == "window_in_flight":
                if await self._recover_worker_failure(decision):
                    continue
                await self._handle_waiting_open(decision)
        await events.emit_manage_charter_tick_scanned(
            roots=len(roots), admitted=admitted
        )

    async def _recover_worker_failure(self, decision: Decision) -> bool:
        """A-R3-1: failed/timeout/closed worker while in-flight → stop root."""
        adm = decision.admission_turn or {}
        meta = window_log.parse_admission_meta(str(adm.get("body") or ""))
        worker_thread = str(meta.get("worker_thread") or "")
        if not worker_thread:
            return False
        try:
            reason = await bus_client.worker_failure_reason(worker_thread)
        except Exception:  # noqa: BLE001 — leave in-flight; soft-remind path
            logger.exception(
                "charter-runner worker-failure probe failed for %s", worker_thread
            )
            return False
        if reason is None:
            return False
        self._caps.mark_failed(decision.root_id, "worker_failed")
        await events.emit_manage_charter_tick_window_failed(
            root=decision.root_id, reason="worker_failed"
        )
        return True

    async def _admit_window(self, decision: Decision, turns: list[dict]) -> bool:
        root_id = decision.root_id
        assert decision.parsed is not None and decision.checkpoint is not None
        if self._workspace_root is None:
            raise RuntimeError(
                "charter-runner requires workspace_root for handoff packets"
            )
        window_index = _count_admissions(turns) + 1
        admission_mode = _admission_mode()
        # A-R3-4: durable pre-fire intent — crash before pointer must not re-fire.
        if self._caps.has_admit_intent(root_id, window_index):
            self._caps.mark_failed(root_id, "admit_intent_orphan")
            await events.emit_manage_charter_tick_window_failed(
                root=root_id, reason="admit_intent_orphan"
            )
            return False
        packet = materialize_resume_packet(
            root_id,
            decision.parsed,
            scoreboard_uri=decision.parsed.scoreboard_uri,
            window_index=window_index,
            admission_mode=admission_mode,
        )
        self._caps.mark_admit_intent(root_id, window_index)
        # Fire first — a failed handoff must not leave an orphaned in-flight pointer.
        try:
            result = await dispatch_client.fire_window(
                root_id,
                packet,
                workspace_root=self._workspace_root,
                window_index=window_index,
                subject=handoff_subject(
                    root_id, window_index, admission_mode=admission_mode
                ),
                admission_mode=admission_mode,
            )
        except Exception:
            self._caps.clear_admit_intent(root_id, window_index)
            raise
        # Admit bookkeeping before pointer post so a failed pointer cannot
        # re-fire on the next tick (A1 — crash-safe vs double-fire).
        self._caps.record_admit(root_id)
        worker_thread = str(result.get("thread_id") or "")
        packet_path = str(result.get("packet_path") or "")
        push = str(result.get("push_reminder") or "")
        now_iso = datetime.now(UTC).isoformat()
        try:
            await bus_client.post_admission_pointer(
                root_id,
                window_index=window_index,
                posted_at_iso=now_iso,
                worker_thread=worker_thread,
                packet_path=packet_path,
            )
        except Exception as exc:  # noqa: BLE001 — stop root; do not re-fire
            logger.exception(
                "charter-runner pointer post failed for root %s after fire: %s",
                root_id,
                exc,
            )
            self._caps.mark_failed(root_id, "pointer_post_failed")
            await events.emit_manage_charter_tick_window_failed(
                root=root_id, reason="pointer_post_failed"
            )
            return False
        self._reminded.discard(root_id)
        await events.emit_manage_charter_tick_admitted(
            root=root_id,
            dispatch_id=str(result.get("dispatch_id") or worker_thread),
            worker_thread=worker_thread,
        )
        try:
            window_log.append_admit(
                root_id=root_id,
                window_index=window_index,
                worker_thread=worker_thread,
                packet_path=packet_path,
                packet_text=packet,
                push_reminder=push,
                dispatch_id=str(result.get("dispatch_id") or ""),
            )
            # Record executor bind in the numbered transcript.
            executor = result.get("executor") or {}
            if executor and worker_thread:
                from .window_log import worker_transcript_path

                note = (
                    f"\n--- executor ---\n"
                    f"seat={executor.get('seat')} model={executor.get('model')} "
                    f"knobs={executor.get('model_knobs')} "
                    f"contract={executor.get('contract')}\n"
                )
                path = worker_transcript_path(worker_thread)
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(note)
        except Exception:  # noqa: BLE001 — transcript must not kill the tick
            logger.exception("charter-runner window_log append_admit failed")
        msg = (
            f"charter-runner: admitted {worker_thread} for root {root_id}"
            + (
                " (attended IDE — open worker thread)"
                if admission_mode == "handoff"
                else " (cursor/grok-4.5 effort=high)"
            )
        )
        if push:
            msg += f" — {push}"
        if self._on_admit is not None:
            try:
                self._on_admit(msg)
            except Exception:  # noqa: BLE001 — notify must not kill the tick
                logger.exception("charter-runner on_admit notify failed")
        return True

    async def _handle_waiting_open(self, decision: Decision) -> None:
        """Soft remind by default; optional unattended hard-fail past stale threshold."""
        adm = decision.admission_turn or {}
        posted_at = _admission_posted_at(adm)
        if posted_at is None:
            return
        age = (datetime.now(UTC) - posted_at).total_seconds()
        stale_s = self._unattended_stale_s
        if stale_s > 0 and age >= stale_s:
            self._caps.mark_failed(decision.root_id, "stale_window")
            await events.emit_manage_charter_tick_window_failed(
                root=decision.root_id, reason="stale_window"
            )
            return
        if age < _WAITING_OPEN_REMIND_S:
            return
        if decision.root_id in self._reminded:
            return
        self._reminded.add(decision.root_id)
        await events.emit_manage_charter_tick_waiting_open(
            root=decision.root_id, age_s=int(age)
        )


def _count_admissions(turns: list[dict]) -> int:
    prefix = ADMISSION_SUBJECT_PREFIX.upper()
    return sum(
        1 for t in turns if str(t.get("subject") or "").upper().startswith(prefix)
    )


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
