"""In-memory idempotency and active-work tracking for cursor-sdk dispatches."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)


class DispatchConflict(Exception):  # noqa: N818 — spec name
    """Raised when dispatch_id fingerprint does not match a prior admission."""


@dataclass(frozen=True, slots=True)
class _DispatchRecord:
    fingerprint: str
    admission: CursorDispatchResponse
    task: asyncio.Task[Any] | None = None


class CursorDispatchRegistry:
    """Process-local singleton; restart loses in-flight dispatch state."""

    _instance: CursorDispatchRegistry | None = None

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[str, _DispatchRecord] = {}

    @classmethod
    def instance(cls) -> CursorDispatchRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def fingerprint(req: CursorDispatchRequest) -> str:
        payload = {
            "thread_id": req.thread_id,
            "model": req.model,
            "dispatch_id": req.dispatch_id,
            "packet_path": req.packet_path,
            "message": req.message,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def admit(
        self,
        dispatch_id: str,
        fingerprint: str,
        admission: CursorDispatchResponse,
    ) -> CursorDispatchResponse | None:
        """Return cached admission on idempotent hit; store new record on miss."""
        async with self._lock:
            existing = self._records.get(dispatch_id)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise DispatchConflict(
                        f"dispatch_id {dispatch_id!r} already admitted with "
                        f"different payload fingerprint"
                    )
                return existing.admission
            self._records[dispatch_id] = _DispatchRecord(
                fingerprint=fingerprint, admission=admission
            )
            return None

    def attach_task(self, dispatch_id: str, task: asyncio.Task[Any]) -> None:
        record = self._records.get(dispatch_id)
        if record is None:
            return
        self._records[dispatch_id] = _DispatchRecord(
            fingerprint=record.fingerprint,
            admission=record.admission,
            task=task,
        )

    def active_snapshot(self) -> dict[str, Any]:
        running_ids: list[str] = []
        for dispatch_id, record in self._records.items():
            task = record.task
            if task is not None and not task.done():
                running_ids.append(dispatch_id)
        return {"running": len(running_ids), "dispatch_ids": running_ids}
