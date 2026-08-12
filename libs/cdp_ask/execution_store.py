"""In-memory execution store with TTL, idle reaper, and boot reconcile (F-1)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from admission_common.qualified_scalar import (
    AuthorityClass,
    QualifiedScalar,
    SurfaceDecl,
    seal,
)

from cdp_ask.models import CompletionPhase, ExecutionStatus, StallStage

_ACTIVE_WORK_SNAPSHOT = "active_work_snapshot"
_RUNNING_COUNT_SCOPE = "cdp_ask execution store, pending/running streams"
_LIVE_CSE_COUNT_SCOPE = "open CSE attachments (Chrome pages), this host"
_ADMISSION_COUNT_SCOPE = "running/stream admissions, this host (soft=2 hard=3)"
_REGISTRY_CAPACITY_SCOPE = "active registry Chrome hosts (ports/profiles), this host"
_EFFECTIVE_COUNT_SCOPE = (
    "restart-drain aggregate max(running_count, live_cse_count); NOT admission"
)

DeregisterFn = Callable[[str], None]

# Operator bind (friction a:25814) + ontology split (arc 6885):
# soft/hard gate **concurrent streams** (running executions), not open tabs.
# Open idle attachments are hygiene once chat_url is recorded.
# Registry Chrome hosts are a separate scarce resource (count_capacity_lanes).
# Drain `busy` stays independent: any pending/running **or** live attachment.
LANE_SOFT_LIMIT = 2
LANE_HARD_LIMIT = 3
_LIVE_CSE_CACHE_TTL_S = 10.0
_REGISTRY_SOURCE = "cse-session-registry"


def _registry_projection(registration_id: str | None) -> dict[str, str | None]:
    """Additive cdp_url/chat_url/source from CSE session registry."""
    if not registration_id:
        return {"cdp_url": None, "chat_url": None, "source": None}
    from claude_bundles import cdp_registry

    chat_url = cdp_registry.chat_url_for_registration(registration_id)
    cdp_url: str | None = None
    for lane in cdp_registry.list_active():
        if lane.registration_id == registration_id:
            cdp_url = lane.cdp_url
            break
    if not chat_url and not cdp_url:
        return {"cdp_url": None, "chat_url": None, "source": None}
    return {
        "cdp_url": cdp_url,
        "chat_url": chat_url,
        "source": _REGISTRY_SOURCE,
    }


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
        self._stop_ack_task: asyncio.Task[None] | None = None
        self._deregister: DeregisterFn | None = None
        self._live_cse_cache: tuple[float, int] | None = None

    def bind_deregister(self, fn: DeregisterFn) -> None:
        self._deregister = fn

    async def start(self) -> None:
        if self._reaper_task is None:
            self._reaper_task = asyncio.create_task(self._reaper_loop())
        if self._stop_ack_task is None:
            self._stop_ack_task = asyncio.create_task(self._stop_ack_checkin_loop())

    async def stop(self) -> None:
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

    def running_registration_ids_snapshot(self) -> set[str]:
        """Best-effort sync read for orphan-scan attach ladder (no await)."""
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
        """Aggregate pending/running executions for drain + stream-admission probes.

        Split (arc 6885):
        - **Admission** (``free_slots`` / ``at_*_limit``): ``running_count`` only
          (soft=2 prefer, hard=3 ceiling) — concurrent streams, not open tabs.
        - **Attachment hygiene**: ``live_cse_count`` (open CSE pages) — disposable
          when ``chat_url`` is recorded; does **not** fill hard limit.
        - **Registry hosts**: ``registry_capacity_count`` — active Chrome ports.
        - **Drain busy**: any in-flight stream **or** observed open attachment.
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
                    **_registry_projection(rec.registration_id),
                }
                for rec in active
            ]
        running_count = len(execution_ids)
        live_cse_count = self._live_cse_count()
        admission_count = running_count
        free_slots = max(0, LANE_HARD_LIMIT - admission_count)
        # Restart-drain aggregate only — never drives at_hard_limit after 6885.
        effective = max(running_count, live_cse_count)
        from claude_bundles import cdp_registry

        registry_capacity_count = cdp_registry.count_capacity_lanes()
        # Restart drain must treat an observed open CSE attachment as in-flight
        # even when no project-ask execution is recorded: Cowork keeps the life
        # MCP connector hot between tool POSTs.
        payload: dict[str, Any] = {
            "busy": running_count > 0 or live_cse_count > 0,
            "execution_ids": execution_ids,
            "rows": rows,
            "soft_limit": LANE_SOFT_LIMIT,
            "hard_limit": LANE_HARD_LIMIT,
            "free_slots": free_slots,
            "at_soft_limit": admission_count >= LANE_SOFT_LIMIT,
            "at_hard_limit": admission_count >= LANE_HARD_LIMIT,
        }
        payload.update(
            QualifiedScalar(
                value=running_count,
                scope=_RUNNING_COUNT_SCOPE,
                authority=AuthorityClass.RECORDED,
            ).emit("running_count")
        )
        payload.update(
            QualifiedScalar(
                value=admission_count,
                scope=_ADMISSION_COUNT_SCOPE,
                authority=AuthorityClass.RECORDED,
            ).emit("admission_count")
        )
        payload.update(
            QualifiedScalar(
                value=live_cse_count,
                scope=_LIVE_CSE_COUNT_SCOPE,
                authority=AuthorityClass.OBSERVED,
            ).emit("live_cse_count")
        )
        payload.update(
            QualifiedScalar(
                value=registry_capacity_count,
                scope=_REGISTRY_CAPACITY_SCOPE,
                authority=AuthorityClass.RECORDED,
            ).emit("registry_capacity_count")
        )
        payload.update(
            QualifiedScalar(
                value=effective,
                scope=_EFFECTIVE_COUNT_SCOPE,
                authority=AuthorityClass.MAX_OF,
            ).emit("effective_count")
        )
        decl = SurfaceDecl(_ACTIVE_WORK_SNAPSHOT)
        decl.plain(
            "busy",
            reason="derived boolean: running_count > 0 or live_cse_count > 0",
        )
        decl.plain("soft_limit", reason="configured stream admission constant")
        decl.plain("hard_limit", reason="configured stream admission constant")
        decl.plain("free_slots", reason="derived: hard_limit - admission_count")
        decl.plain("at_soft_limit", reason="derived: admission_count >= soft_limit")
        decl.plain("at_hard_limit", reason="derived: admission_count >= hard_limit")
        return seal(payload, decl)

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

    async def iter_stop_ack_candidates(self, now: float) -> list[ExecutionRecord]:
        """Return mission stream-stop records past quiet gate (F1 predicate)."""
        from cdp_ask.stop_ack_checkin import is_stop_ack_candidate

        async with self._lock:
            return [
                rec
                for rec in self._records.values()
                if is_stop_ack_candidate(rec, now)
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
