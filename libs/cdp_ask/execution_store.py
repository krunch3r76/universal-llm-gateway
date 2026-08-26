"""In-memory execution store for execution records, TTL cleanup, and boot recovery.

Admission reads remain limited to recorded pending/running executions. Browser
attachment occupancy is supplied by the separate asynchronous projection.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from admission_common.qualified_scalar import seal

from cdp_ask.lane_admission import LANE_HARD_LIMIT, LANE_SOFT_LIMIT
from cdp_ask.models import CompletionPhase, ExecutionStatus, StallStage

__all__ = ["ExecutionRecord", "ExecutionStore", "LANE_HARD_LIMIT", "LANE_SOFT_LIMIT"]

DeregisterFn = Callable[[str], None]


# Operator bind (friction a:25814) + ontology split (arc 6885):
# soft/hard gate **concurrent streams** (running executions), not open tabs.
# Open idle attachments are hygiene once chat_url is recorded.
@dataclass
class ExecutionRecord:
    execution_id: str
    status: ExecutionStatus
    created_at: float
    updated_at: float
    registration_id: str | None = None
    stargate_execution_id: str | None = None
    holder: str = ""
    purpose: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    abort_requested: bool = False
    task: asyncio.Task[Any] | None = field(default=None, repr=False)
    completion_phase: CompletionPhase = "running"
    content_proof_uri: str | None = None
    content_proof_sha256: str | None = None
    turn_idle_at: float | None = None
    stall_stage: StallStage | None = None
    streaming: bool | None = None
    stop: bool | None = None
    tool_pause: bool | None = None
    liveness_observed_at: float | None = None
    parent_thread: str | None = None
    mission_kind: str | None = None


class ExecutionStore:
    """Track async project-ask executions with TTL, idle cleanup, and projection hooks for restart-safety read models."""

    def __init__(
        self,
        *,
        execution_ttl_s: float = 7200.0,
        idle_ttl_s: float = 900.0,
        reaper_interval_s: float = 30.0,
    ) -> None:
        self._records: dict[str, ExecutionRecord] = {}
        self._by_stargate: dict[str, str] = {}
        self._execution_ttl_s = execution_ttl_s
        self._idle_ttl_s = idle_ttl_s
        self._reaper_interval_s = reaper_interval_s
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None
        self._stop_ack_task: asyncio.Task[None] | None = None
        self._deregister: DeregisterFn | None = None
        self._occupancy: Any | None = None

    def bind_deregister(self, fn: DeregisterFn) -> None:
        self._deregister = fn

    def bind_occupancy(self, occupancy: Any) -> None:
        """Bind the background occupancy projection used only by drain state."""
        self._occupancy = occupancy

    def request_occupancy_refresh(self) -> None:
        """Wake occupancy sensing after a registry-affecting execution change."""
        if self._occupancy is not None:
            self._occupancy.request_refresh()

    async def start(self) -> None:
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop())
        if self._stop_ack_task is None:
            self._stop_ack_task = asyncio.create_task(self._stop_ack_checkin_loop())
        if self._occupancy is not None:
            await self._occupancy.start()

    async def stop(self) -> None:
        if self._occupancy is not None:
            await self._occupancy.stop()
        if self._stop_ack_task is not None:
            self._stop_ack_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stop_ack_task
            self._stop_ack_task = None
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None

    async def create(
        self,
        *,
        holder: str,
        purpose: str | None,
        parent_thread: str | None = None,
        mission_kind: str | None = None,
        stargate_execution_id: str | None = None,
    ) -> ExecutionRecord:
        now = time.time()
        execution_id = uuid.uuid4().hex
        stargate = (stargate_execution_id or "").strip() or None
        record = ExecutionRecord(
            execution_id=execution_id,
            status="pending",
            created_at=now,
            updated_at=now,
            holder=holder,
            purpose=purpose,
            stargate_execution_id=stargate,
            parent_thread=(str(parent_thread).strip() or None)
            if parent_thread
            else None,
            mission_kind=(str(mission_kind).strip() or None) if mission_kind else None,
        )
        async with self._lock:
            self._records[execution_id] = record
            if stargate:
                self._by_stargate[stargate] = execution_id
        return record

    async def get(self, execution_id: str) -> ExecutionRecord | None:
        token = (execution_id or "").strip()
        async with self._lock:
            rec = self._records.get(token)
            if rec is not None:
                return rec
            aliased = self._by_stargate.get(token)
            if aliased is None:
                return None
            return self._records.get(aliased)

    async def list_running_registration_ids(self) -> set[str]:
        async with self._lock:
            return {
                rec.registration_id
                for rec in self._records.values()
                if rec.registration_id and rec.status in {"pending", "running"}
            }

    def running_registration_ids_snapshot(self) -> set[str]:
        """Best-effort sync read for orphan-scan attach ladder (no await)."""
        return {
            rec.registration_id
            for rec in self._records.values()
            if rec.registration_id and rec.status in {"pending", "running"}
        }

    async def _active_rows_snapshot(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Copy pending/running rows for the admission and drain read models."""
        from cdp_ask.work_projection import active_rows

        async with self._lock:
            return active_rows(self._records.values())

    async def active_work_snapshot(self) -> dict[str, Any]:
        """Return recorded executions, stream-admission capacity, and listable seats.

        ``seated_rows`` is always a list (including ``[]``), projected from this
        process's registry ``load_active()``. ``seat_rows`` is the same read,
        seat-open-gated, address-free. Admission scalars stay on the
        execution store; identity consumers read ``seated_rows`` so MCP's
        attach early-out consumes Jupiter seats instead of overlaying a hub
        empty file. ``free_slots`` remains stream admission; X occupancy is
        attached as ``x_*`` on the same snapshot and does not rewrite that
        formula.
        """
        from claude_bundles.cdp_registry_store import load_active
        from claude_bundles.hop_cadence_seat_snap import (
            attach_seated_rows,
            attach_seat_rows,
            seated_rows_from_registry_records,
            seat_rows_from_registry_records,
        )
        from claude_bundles.x_display_capacity import attach_x_display_capacity

        from cdp_ask.work_projection import admission_projection

        rows, execution_ids = await self._active_rows_snapshot()
        payload, decl = admission_projection(rows, execution_ids)
        try:
            raw = load_active()
            seated = seated_rows_from_registry_records(raw)
            seat = seat_rows_from_registry_records(raw)
        except Exception:  # noqa: BLE001 — identity attach must not break admission
            seated = []
            seat = []
        payload = attach_seated_rows(payload, seated)
        payload = attach_seat_rows(payload, seat)
        attach_x_display_capacity(payload, decl)
        return seal(payload, decl)

    async def drain_state_snapshot(self) -> dict[str, Any]:
        """Return cached browser occupancy plus recorded execution drain state."""
        from cdp_ask.work_projection import drain_projection

        rows, execution_ids = await self._active_rows_snapshot()
        return drain_projection(rows, execution_ids, self._occupancy)

    async def attach_task(self, execution_id: str, task: asyncio.Task[Any]) -> None:
        async with self._lock:
            rec = self._records.get(execution_id)
            if rec is None:
                return
            rec.task = task
            rec.status = "running"
            rec.updated_at = time.time()

    async def set_registration_id(
        self, execution_id: str, registration_id: str
    ) -> None:
        async with self._lock:
            rec = self._records.get(execution_id)
            if rec is None:
                return
            rec.registration_id = registration_id
            rec.updated_at = time.time()
        self.request_occupancy_refresh()

    async def update_ladder(
        self,
        execution_id: str,
        *,
        completion_phase: CompletionPhase | None = None,
        content_proof_uri: str | None = None,
        content_proof_sha256: str | None = None,
        turn_idle_at: float | None = None,
        stall_stage: StallStage | None = None,
    ) -> None:
        """Merge dual-completion ladder fields on an in-flight execution."""
        async with self._lock:
            rec = self._records.get(execution_id)
            if rec is None:
                return
            if completion_phase is not None:
                rec.completion_phase = completion_phase
            if content_proof_uri is not None:
                rec.content_proof_uri = content_proof_uri
            if content_proof_sha256 is not None:
                rec.content_proof_sha256 = content_proof_sha256
            if turn_idle_at is not None:
                rec.turn_idle_at = turn_idle_at
            if stall_stage is not None:
                rec.stall_stage = stall_stage
            rec.updated_at = time.time()

    async def update_liveness(
        self,
        execution_id: str,
        *,
        streaming: bool | None,
        stop: bool | None,
        tool_pause: bool | None,
        liveness_observed_at: float | None,
    ) -> None:
        """Persist the last Cowork window harvest sample for poll-plane projection."""
        async with self._lock:
            rec = self._records.get(execution_id)
            if rec is None:
                return
            rec.streaming = streaming
            rec.stop = stop
            rec.tool_pause = tool_pause
            rec.liveness_observed_at = liveness_observed_at
            rec.updated_at = time.time()

    async def mark_terminal(
        self,
        execution_id: str,
        *,
        status: ExecutionStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        completion_phase: CompletionPhase | None = None,
        stall_stage: StallStage | None = None,
    ) -> None:
        async with self._lock:
            rec = self._records[execution_id]
            rec.status = status
            rec.result = result
            rec.error = error
            rec.updated_at = time.time()
            rec.task = None
            if completion_phase is not None:
                rec.completion_phase = completion_phase
            elif status == "completed":
                rec.completion_phase = "terminal"
            elif status in {"failed", "aborted"}:
                rec.completion_phase = "failed"
            if stall_stage is not None:
                rec.stall_stage = stall_stage
            elif status == "failed" and rec.stall_stage is None:
                rec.stall_stage = "unknown"
            rec.streaming = None
            rec.stop = None
            rec.tool_pause = None
            rec.liveness_observed_at = None
        self.request_occupancy_refresh()

    async def mark_awaiting_wake(
        self,
        execution_id: str,
        *,
        result: dict[str, Any],
    ) -> None:
        """Nonterminal harvest complete while CSR wake debt retains the lane."""
        async with self._lock:
            rec = self._records.get(execution_id)
            if rec is None:
                return
            rec.status = "running"
            rec.result = result
            rec.error = None
            rec.updated_at = time.time()
            rec.task = None
            rec.completion_phase = "awaiting_wake"
            rec.streaming = None
            rec.stop = None
            rec.tool_pause = None
            rec.liveness_observed_at = None

    async def request_abort(self, execution_id: str) -> ExecutionRecord | None:
        async with self._lock:
            rec = self._records.get(execution_id)
            if rec is None:
                return None
            rec.abort_requested = True
            rec.updated_at = time.time()
            return rec

    async def boot_reconcile(self) -> list[str]:
        """Plan+apply boot lane re-adoption; return orphaned registration_ids."""
        from claude_bundles import boot_lane_readoption as blr
        from claude_bundles import cdp_orphans, cdp_registry
        from claude_bundles.cse_wake_retain import registration_has_wake_debt

        live_exec = await self.list_running_registration_ids()

        def _reconcile_sync() -> list[str]:
            plan = blr.plan_boot_lane_readoption(
                cdp_registry._load_active(),
                cdp_orphans.probe_live_ports(),
                running_registration_ids=set(live_exec),
                wake_debt=registration_has_wake_debt,
            )
            _, orphaned = blr.apply_boot_readoption_plan(plan)
            return orphaned

        return await asyncio.to_thread(_reconcile_sync)

    async def iter_stop_ack_candidates(self, now: float) -> list[ExecutionRecord]:
        """Return mission stream-stop records past quiet gate (F1 predicate)."""
        from cdp_ask.stop_ack_checkin import is_stop_ack_candidate

        async with self._lock:
            return [
                rec for rec in self._records.values() if is_stop_ack_candidate(rec, now)
            ]

    async def _stop_ack_checkin_loop(self) -> None:
        while True:
            await asyncio.sleep(self._reaper_interval_s)
            await self._stop_ack_tick_once()

    async def _stop_ack_tick_once(self) -> None:
        from cdp_ask.stop_ack_checkin import run_checkin_tick

        await run_checkin_tick(self)

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(self._reaper_interval_s)
            await self._reap_once()

    async def _reap_once(self) -> None:
        from claude_bundles.operator_proxy_mission import (
            is_operator_proxy_mission_purpose,
        )

        now = time.time()
        expired: list[ExecutionRecord] = []
        async with self._lock:
            for rec in list(self._records.values()):
                age = now - rec.created_at
                idle = now - rec.updated_at
                if rec.status in {"pending", "running"} and age > self._execution_ttl_s:
                    # Operator-proxy / mission seats outlive Stargate wall via
                    # mission_retain; satellite TTL must not schedule false death
                    # (arc 6893 — ttl_kills_op_while_cse_alive).
                    if is_operator_proxy_mission_purpose(rec.purpose):
                        continue
                    expired.append(rec)
                    continue
                if (
                    rec.status in {"completed", "failed", "aborted"}
                    and idle > self._idle_ttl_s
                ):
                    self._records.pop(rec.execution_id, None)
                    if rec.stargate_execution_id:
                        self._by_stargate.pop(rec.stargate_execution_id, None)

        for rec in expired:
            if rec.task and not rec.task.done():
                rec.task.cancel()
            await self.mark_terminal(
                rec.execution_id,
                status="failed",
                error="execution TTL exceeded",
            )
            if rec.registration_id:
                self._safe_deregister(rec.registration_id)

    def _safe_deregister(self, registration_id: str) -> None:
        from claude_bundles.cse_wake_retain import registration_has_wake_debt

        if registration_has_wake_debt(registration_id):
            return
        from claude_bundles import cdp_registry

        with contextlib.suppress(Exception):
            cdp_registry.deregister_lane(registration_id, reason="probe_failed")
        self.request_occupancy_refresh()
