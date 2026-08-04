"""In-memory execution store with TTL, idle reaper, and boot reconcile (F-1)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cdp_ask.models import CompletionPhase, ExecutionStatus, StallStage

DeregisterFn = Callable[[str], None]

# Operator bind (friction a:25814): simultaneous CDP project-ask lanes.
# soft = prefer-not-to-add; hard = lane-full for admission. Drain `busy` stays
# independent: any pending/running work defers restart.
LANE_SOFT_LIMIT = 2
LANE_HARD_LIMIT = 3
_LIVE_CSE_CACHE_TTL_S = 10.0


@dataclass
class ExecutionRecord:
    execution_id: str
    status: ExecutionStatus
    created_at: float
    updated_at: float
    registration_id: str | None = None
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


class ExecutionStore:
    """Track async project-ask executions with TTL + idle cleanup."""

    def __init__(
        self,
        *,
        execution_ttl_s: float = 7200.0,
        idle_ttl_s: float = 900.0,
        reaper_interval_s: float = 30.0,
    ) -> None:
        self._records: dict[str, ExecutionRecord] = {}
        self._execution_ttl_s = execution_ttl_s
        self._idle_ttl_s = idle_ttl_s
        self._reaper_interval_s = reaper_interval_s
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None
        self._deregister: DeregisterFn | None = None
        self._live_cse_cache: tuple[float, int] | None = None

    def bind_deregister(self, fn: DeregisterFn) -> None:
        self._deregister = fn

    async def start(self) -> None:
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop())

    async def stop(self) -> None:
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
    ) -> ExecutionRecord:
        now = time.time()
        execution_id = uuid.uuid4().hex
        record = ExecutionRecord(
            execution_id=execution_id,
            status="pending",
            created_at=now,
            updated_at=now,
            holder=holder,
            purpose=purpose,
        )
        async with self._lock:
            self._records[execution_id] = record
        return record

    async def get(self, execution_id: str) -> ExecutionRecord | None:
        async with self._lock:
            return self._records.get(execution_id)

    async def list_running_registration_ids(self) -> set[str]:
        async with self._lock:
            return {
                rec.registration_id
                for rec in self._records.values()
                if rec.registration_id and rec.status in {"pending", "running"}
            }

    def _live_cse_count(self) -> int:
        now = time.time()
        cached = self._live_cse_cache
        if cached is not None and now - cached[0] < _LIVE_CSE_CACHE_TTL_S:
            return cached[1]
        from claude_bundles import cdp_orphans

        count = sum(1 for port in cdp_orphans.probe_live_ports() if port.has_live_cse)
        self._live_cse_cache = (now, count)
        return count

    async def active_work_snapshot(self) -> dict[str, Any]:
        """Aggregate pending/running executions for drain + lane-admission probes.

        ``busy`` is restart-drain semantics (any in-flight **or** observed live CSE)
        — NOT lane-full. Seats admit a new CDP lane from ``free_slots`` /
        ``at_hard_limit`` (soft=2 prefer, hard=3 ceiling; friction a:25814).
        """
        async with self._lock:
            active = [
                rec
                for rec in self._records.values()
                if rec.status in {"pending", "running"}
            ]
            execution_ids = [rec.execution_id for rec in active]
            rows = [
                {
                    "execution_id": rec.execution_id,
                    "registration_id": rec.registration_id,
                    "holder": rec.holder,
                    "purpose": rec.purpose,
                    "status": rec.status,
                }
                for rec in active
            ]
        running_count = len(execution_ids)
        live_cse_count = self._live_cse_count()
        effective = max(running_count, live_cse_count)
        free_slots = max(0, LANE_HARD_LIMIT - effective)
        # Restart drain must treat an observed live CSE as in-flight work even
        # when no project-ask execution is recorded: Cowork keeps the life MCP
        # connector hot between tool POSTs, and killing MCP (or cdp_ask) mid-turn
        # drops that session. Lane admission still uses free_slots / at_hard_limit.
        return {
            "busy": effective > 0,
            "running_count": running_count,
            "running_count_authority": "recorded",
            "live_cse_count": live_cse_count,
            "live_cse_count_authority": "observed",
            "effective_count": effective,
            "effective_count_authority": "max(recorded, observed)",
            "execution_ids": execution_ids,
            "rows": rows,
            "soft_limit": LANE_SOFT_LIMIT,
            "hard_limit": LANE_HARD_LIMIT,
            "free_slots": free_slots,
            "at_soft_limit": effective >= LANE_SOFT_LIMIT,
            "at_hard_limit": effective >= LANE_HARD_LIMIT,
        }

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
        """Deregister registry lanes with no live execution after restart."""
        live = await self.list_running_registration_ids()
        reaped: list[str] = []
        from claude_bundles import cdp_registry

        active_rows = cdp_registry._load_active()
        for registration_id, row in active_rows.items():
            if row.get("status") != "active":
                continue
            if registration_id in live:
                continue
            from claude_bundles.cse_wake_retain import registration_has_wake_debt

            if registration_has_wake_debt(registration_id):
                continue
            cdp_registry.deregister_lane(registration_id, reason="probe_failed")
            reaped.append(registration_id)
        return reaped

    async def _reaper_loop(self) -> None:
        while True:
            await asyncio.sleep(self._reaper_interval_s)
            await self._reap_once()

    async def _reap_once(self) -> None:
        now = time.time()
        expired: list[ExecutionRecord] = []
        async with self._lock:
            for rec in list(self._records.values()):
                age = now - rec.created_at
                idle = now - rec.updated_at
                if rec.status in {"pending", "running"} and age > self._execution_ttl_s:
                    expired.append(rec)
                    continue
                if (
                    rec.status in {"completed", "failed", "aborted"}
                    and idle > self._idle_ttl_s
                ):
                    self._records.pop(rec.execution_id, None)

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
