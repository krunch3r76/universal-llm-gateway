"""In-memory execution store with TTL, idle reaper, and boot reconcile (F-1)."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cdp_ask.models import ExecutionStatus

DeregisterFn = Callable[[str], None]


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
                if rec.registration_id
                and rec.status in {"pending", "running"}
            }

    async def active_work_snapshot(self) -> dict[str, Any]:
        """Aggregate pending/running executions for drain-aware restart probes."""
        async with self._lock:
            execution_ids = [
                rec.execution_id
                for rec in self._records.values()
                if rec.status in {"pending", "running"}
            ]
        running_count = len(execution_ids)
        return {
            "busy": running_count > 0,
            "running_count": running_count,
            "execution_ids": execution_ids,
        }

    async def attach_task(self, execution_id: str, task: asyncio.Task[Any]) -> None:
        async with self._lock:
            rec = self._records.get(execution_id)
            if rec is None:
                return
            rec.task = task
            rec.status = "running"
            rec.updated_at = time.time()

    async def set_registration_id(self, execution_id: str, registration_id: str) -> None:
        async with self._lock:
            rec = self._records.get(execution_id)
            if rec is None:
                return
            rec.registration_id = registration_id
            rec.updated_at = time.time()

    async def mark_terminal(
        self,
        execution_id: str,
        *,
        status: ExecutionStatus,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        async with self._lock:
            rec = self._records[execution_id]
            rec.status = status
            rec.result = result
            rec.error = error
            rec.updated_at = time.time()
            rec.task = None

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

        for reg in cdp_registry.list_active():
            if reg.registration_id in live:
                continue
            if self._deregister is not None:
                self._deregister(reg.registration_id)
            else:
                cdp_registry.deregister_lane(reg.registration_id, kill=True)
            reaped.append(reg.registration_id)
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
                if rec.status in {"completed", "failed", "aborted"} and idle > self._idle_ttl_s:
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
        if self._deregister is not None:
            self._deregister(registration_id)
            return
        from claude_bundles import cdp_registry

        with contextlib.suppress(Exception):
            cdp_registry.deregister_lane(registration_id, kill=True)
