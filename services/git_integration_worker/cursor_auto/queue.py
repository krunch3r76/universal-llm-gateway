"""In-memory Auto job queue for admit-on-request enqueue."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AutoJob:
    """One ``lane:cursor-auto`` request awaiting / claimed by Auto."""

    job_id: str
    thread_id: str
    turn_number: int
    subject: str
    body: str
    from_agent: str
    to_agent: str
    desired_model: str
    desired_effort: str
    contract: str
    enqueued_at: float = field(default_factory=time.monotonic)
    status: str = "queued"  # queued | claimed | done | failed


class AutoJobQueue:
    """Thread-safe FIFO of Auto jobs (process-local v0)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, AutoJob] = {}
        self._order: list[str] = []

    def enqueue(self, **kwargs: Any) -> AutoJob:
        job = AutoJob(job_id=str(uuid.uuid4()), **kwargs)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
        return job

    def claim_next(self) -> AutoJob | None:
        with self._lock:
            for jid in self._order:
                job = self._jobs[jid]
                if job.status == "queued":
                    job.status = "claimed"
                    return job
        return None

    def mark_done(self, job_id: str, *, failed: bool = False) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.status = "failed" if failed else "done"

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "queued")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "pending": sum(1 for j in self._jobs.values() if j.status == "queued"),
                "claimed": sum(1 for j in self._jobs.values() if j.status == "claimed"),
                "done": sum(1 for j in self._jobs.values() if j.status == "done"),
                "failed": sum(1 for j in self._jobs.values() if j.status == "failed"),
                "total": len(self._jobs),
            }


_QUEUE = AutoJobQueue()


def get_queue() -> AutoJobQueue:
    """Return the process-global Auto job queue used by enqueue and workers."""
    return _QUEUE
