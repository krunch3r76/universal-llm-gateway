"""In-memory Auto job queue for admit-on-request enqueue (write-through ledger)."""

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
    require_attended: bool = False
    # Caller-supplied idempotency key, echoed enqueue→closeout (Fable §5).
    request_id: str | None = None
    enqueued_at: float = field(default_factory=time.monotonic)
    status: str = "queued"  # queued | claimed | done | failed | superseded
    superseded_by: str | None = None
    supersedes: str | None = None
    superseded_dispatch_id: str | None = None


class AutoJobQueue:
    """Thread-safe FIFO of Auto jobs with optional durable write-through."""

    def __init__(self, *, durable: bool = True) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, AutoJob] = {}
        self._order: list[str] = []
        self._durable = durable
        self._ledger: Any | None = None

    def _ledger_client(self) -> Any | None:
        if not self._durable:
            return None
        if self._ledger is None:
            from services.git_integration_worker.cursor_auto.job_ledger import (
                get_ledger,
            )

            self._ledger = get_ledger()
        return self._ledger

    def enqueue(self, **kwargs: Any) -> AutoJob:
        job = AutoJob(job_id=str(uuid.uuid4()), **kwargs)
        with self._lock:
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.insert(job)
        return job

    def claim_next(self) -> AutoJob | None:
        with self._lock:
            for jid in self._order:
                job = self._jobs[jid]
                if job.status == "queued":
                    job.status = "claimed"
                    claimed = job
                    break
            else:
                return None
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.mark_claimed(claimed.job_id)
        return claimed

    def bump_heartbeat(self, job_id: str) -> None:
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.bump_heartbeat(job_id)

    def mark_done(self, job_id: str, *, failed: bool = False) -> None:
        """Terminalize a job; a superseded job keeps its interrupt status."""
        terminal_status: str | None = None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status == "superseded":
                return
            job.status = "failed" if failed else "done"
            terminal_status = job.status
        ledger = self._ledger_client()
        if ledger is not None and terminal_status is not None:
            ledger.mark_terminal(
                job_id,
                status=terminal_status,
                terminal_reason=None,
            )

    def get(self, job_id: str) -> AutoJob | None:
        """Return the job record for *job_id*, if the process still holds it."""
        with self._lock:
            return self._jobs.get(job_id)

    def claimed_for_thread(self, thread_id: str) -> AutoJob | None:
        """Return the in-flight job on *thread_id* — the supersede candidate."""
        with self._lock:
            for jid in self._order:
                job = self._jobs[jid]
                if job.thread_id == thread_id and job.status == "claimed":
                    return job
        return None

    def mark_superseded(self, job_id: str, *, superseded_by: str) -> AutoJob | None:
        """Flag an in-flight job as displaced by *superseded_by* and return it."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.status = "superseded"
            job.superseded_by = superseded_by
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.mark_terminal(job_id, status="superseded", terminal_reason="superseded")
            ledger.sync_record(job)
        return job

    def is_superseded(self, job_id: str) -> bool:
        """True once a newer same-thread request has displaced this job."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job is not None and job.status == "superseded"

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "queued")

    def list_open_jobs(self) -> list[AutoJob]:
        with self._lock:
            return [
                job
                for job in self._jobs.values()
                if job.status in ("queued", "claimed")
            ]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = {
                "pending": sum(
                    1 for j in self._jobs.values() if j.status == "queued"
                ),
                "claimed": sum(
                    1 for j in self._jobs.values() if j.status == "claimed"
                ),
                "done": sum(1 for j in self._jobs.values() if j.status == "done"),
                "failed": sum(
                    1 for j in self._jobs.values() if j.status == "failed"
                ),
                "superseded": sum(
                    1 for j in self._jobs.values() if j.status == "superseded"
                ),
                "total": len(self._jobs),
            }
        ledger = self._ledger_client()
        if ledger is not None:
            durable = ledger.status_counts()
            snap["failed_on_restart"] = durable.get("failed_on_restart", 0)
            snap["durable_total"] = durable.get("total", 0)
        return snap


_QUEUE = AutoJobQueue(durable=True)


def get_queue() -> AutoJobQueue:
    """Return the process-global Auto job queue used by enqueue and workers."""
    return _QUEUE


def reset_queue_for_tests(*, durable: bool = True) -> AutoJobQueue:
    """Replace the process-global queue (hermetic tests only)."""
    global _QUEUE
    _QUEUE = AutoJobQueue(durable=durable)
    return _QUEUE
