"""In-memory Auto job queue for admit-on-request enqueue (write-through ledger)."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from services.git_integration_worker.cursor_auto.execution_mode import (
    is_concurrent_execution_mode,
)


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
    escalation: str | None = None
    require_attended: bool = False
    # Caller-supplied idempotency key, echoed enqueue→closeout (Fable §5).
    request_id: str | None = None
    # Captured attached-lane identity for park-on-WAKE leg (b) delivery (B1).
    cse_chat_url: str | None = None
    cse_registration_id: str | None = None
    # Row 21: structural continuity-hop classification (set at enqueue).
    continuity_hop: bool = False
    continuity_matched_token: str | None = None
    # Wire keys dropped at L1 enqueue (``extra=ignore`` observation).
    wire_dropped_fields: tuple[str, ...] = ()
    # Sealed advisor brief for CDP escalation (explicit; not job.body).
    prompt_uri: str | None = None
    advisor_brief: str | None = None
    # GIW checkout-isolation lane (``A``|``B``); None ⇒ select_lane defaults.
    lane: str | None = None
    # Declared execution mode (S-3). "serial" (default) uses the exclusive
    # single-occupant loop unchanged since before this mission. Any other
    # value is looked up against the default-deny allowlist in
    # execution_mode.py -- never branch on this field directly outside that
    # module.
    execution_mode: str = "serial"
    enqueued_at: float = field(default_factory=time.monotonic)
    status: str = (
        "queued"  # queued | claimed | done | failed | report_undelivered | superseded
    )
    superseded_by: str | None = None
    supersedes: str | None = None
    superseded_dispatch_id: str | None = None
    # Nested SDK poll reached terminal; CLOSEOUT may still be in flight.
    # Cleared only by process death — not a success terminal (mark_done stays
    # after closeout). Supersede must not treat this job as in-flight.
    nested_sdk_finished: bool = False


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
                if job.status == "queued" and not is_concurrent_execution_mode(
                    job.execution_mode
                ):
                    job.status = "claimed"
                    claimed = job
                    break
            else:
                return None
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.mark_claimed(claimed.job_id)
        return claimed

    def claim_next_concurrent(self) -> AutoJob | None:
        """Claim the oldest queued job whose execution_mode has been opted
        into concurrent admission. Never claims a serial-class job -- the
        exclusivity invariant of claim_next()/auto_worker_loop is untouched
        by this method; it is a fully independent claim path (same shape as
        the existing continuity-hop bypass, generalized past hops).
        """
        with self._lock:
            for jid in self._order:
                job = self._jobs[jid]
                if job.status == "queued" and is_concurrent_execution_mode(
                    job.execution_mode
                ):
                    job.status = "claimed"
                    claimed = job
                    break
            else:
                return None
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.mark_claimed(claimed.job_id)
        return claimed

    def claim_job(self, job_id: str) -> AutoJob | None:
        """Claim a specific queued job (continuity-hop concurrent path)."""
        if drain_claim_gate_blocks():
            return None
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "queued":
                return None
            job.status = "claimed"
            claimed = job
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.mark_claimed(claimed.job_id)
        return claimed

    def requeue_rehydrated(self, job: AutoJob) -> None:
        """Re-admit a durable queued-never-claimed row into the live FIFO after
        restart. Caller (job_reconcile) already verified eligibility (S-2 ii
        successor check, generation cap) and patched ledger provenance via
        ``merge_record_json``. This method only makes the row visible again to
        ``claim_next`` / ``supersede_candidate_for_thread`` — it must NOT call
        ``ledger.insert`` (the row already exists there from the original
        enqueue; a second insert would violate the ``job_id`` PRIMARY KEY).
        Idempotent: a job_id already resident is a no-op (defends a reconcile
        that somehow runs twice against the same durable row).
        """
        with self._lock:
            if job.job_id in self._jobs:
                return
            self._jobs[job.job_id] = job
            self._order.append(job.job_id)

    def bump_heartbeat(self, job_id: str) -> None:
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.bump_heartbeat(job_id)

    def mark_done(
        self,
        job_id: str,
        *,
        failed: bool = False,
        terminal_reason: str | None = None,
    ) -> None:
        """Terminalize a job; a superseded job keeps its interrupt status."""
        if failed and not terminal_reason:
            raise ValueError("terminal_reason required when failed=True")
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
                terminal_reason=terminal_reason,
            )

    def mark_report_undelivered(
        self,
        job_id: str,
        *,
        terminal_reason: str,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        """Work finished but the terminal bus post did not land (arc 6655)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status == "superseded":
                return
            job.status = "report_undelivered"
        ledger = self._ledger_client()
        if ledger is not None:
            ledger.mark_terminal(
                job_id,
                status="report_undelivered",
                terminal_reason=terminal_reason,
            )
            patch: dict[str, Any] = {"terminal_post_retryable": retryable}
            if status_code is not None:
                patch["terminal_post_status_code"] = status_code
            ledger.merge_record_json(job_id, patch)

    def get(self, job_id: str) -> AutoJob | None:
        """Return the job record for *job_id*, if the process still holds it."""
        with self._lock:
            return self._jobs.get(job_id)

    def mark_nested_sdk_finished(self, job_id: str) -> None:
        """Flag that nested SDK work ended — exclude from supersede, keep claimed.

        Does **not** call ``mark_done``. Relay death must leave a noticeable
        non-done status; only CLOSEOUT success/failure terminalizes the job.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.nested_sdk_finished = True

    def claimed_for_thread(self, thread_id: str) -> AutoJob | None:
        """Return the claimed in-flight job on *thread_id* (claimed-only).

        Jobs whose nested SDK already finished (CLOSEOUT still relaying) stay
        ``claimed`` for restart triage but are not supersede candidates.
        Hop harvest uses :meth:`incumbent_for_thread` so queued commissions
        are named; this helper stays claimed-only.
        """
        with self._lock:
            for jid in self._order:
                job = self._jobs[jid]
                if (
                    job.thread_id == thread_id
                    and job.status == "claimed"
                    and not job.nested_sdk_finished
                ):
                    return job
        return None

    def claimed_for_home_lane(self, lane: str) -> AutoJob | None:
        """Return the claimed job whose operator mailbox aliases to *lane*.

        Mirrors :meth:`claimed_for_thread` but keys on the home lane a
        ``cdp-operator-{lane}-*`` mailbox resolves to (see
        ``hop_cadence_home_lane.home_lane_from_mailbox``), not the literal
        ``job.thread_id``. A conductor commissioned onto a mission-root
        thread other than the operator's own standing thread is otherwise
        invisible to a probe keyed by the aliased watch row's home lane.
        """
        from services.git_integration_worker.cursor_auto.hop_cadence_home_lane import (
            home_lane_from_mailbox,
        )

        with self._lock:
            for jid in self._order:
                job = self._jobs[jid]
                if (
                    job.status == "claimed"
                    and not job.nested_sdk_finished
                    and home_lane_from_mailbox(job.from_agent) == lane
                ):
                    return job
        return None

    def _incumbent_unlocked(
        self,
        thread_id: str,
        *,
        exclude_job_id: str | None = None,
    ) -> AutoJob | None:
        """Claimed in-flight first, else oldest queued. Caller holds the lock."""
        queued_candidate: AutoJob | None = None
        for jid in self._order:
            if exclude_job_id is not None and jid == exclude_job_id:
                continue
            job = self._jobs[jid]
            if job.thread_id != thread_id:
                continue
            if job.status == "claimed" and not job.nested_sdk_finished:
                return job
            if job.status == "queued" and queued_candidate is None:
                queued_candidate = job
        return queued_candidate

    def incumbent_for_thread(
        self,
        thread_id: str,
        *,
        exclude_job_id: str | None = None,
    ) -> AutoJob | None:
        """Return the live Auto commission on *thread_id* for hop harvest.

        Same FIFO as :meth:`supersede_candidate_for_thread` (claimed in-flight
        first, else oldest queued) so a queued window is not reported as a
        clear lane. Pass *exclude_job_id* so a just-enqueued hop does not
        name itself. Reporting only — hop still does not supersede.
        """
        with self._lock:
            return self._incumbent_unlocked(thread_id, exclude_job_id=exclude_job_id)

    def supersede_candidate_for_thread(self, thread_id: str) -> AutoJob | None:
        """Return the same-thread supersede target: claimed in-flight, else queued.

        Scans ``_order`` FIFO. Prefer the first ``claimed`` job that is not
        past nested SDK terminal; otherwise the oldest ``queued`` peer. Contract
        is irrelevant to candidacy.
        """
        with self._lock:
            return self._incumbent_unlocked(thread_id)

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
            ledger.mark_terminal(
                job_id, status="superseded", terminal_reason="superseded"
            )
            ledger.sync_record(job)
        return job

    def is_superseded(self, job_id: str) -> bool:
        """True once a newer same-thread request has displaced this job."""
        with self._lock:
            job = self._jobs.get(job_id)
            return job is not None and job.status == "superseded"

    def claimed_occupancy_ops(self) -> list[dict[str, Any]]:
        """Snapshot claimed in-process Auto jobs as drain occupancy rows.

        Drain idle and ``/active-work`` busy union these with admission tickets
        and live cursor-sdk dispatches. Queued (not yet claimed) jobs are
        omitted — they are not executing in this process. ``nested_sdk_finished``
        jobs stay listed while status remains ``claimed`` (CLOSEOUT still in
        flight). Callers must not hold ``self._lock``.
        """
        ledger = self._ledger_client()
        with self._lock:
            claimed = [job for job in self._jobs.values() if job.status == "claimed"]
        rows: list[dict[str, Any]] = []
        for job in claimed:
            entry: dict[str, Any] = {
                "kind": "cursor-auto",
                "op_id": job.job_id,
                "route": "cursor-auto/claimed",
                "state": "running",
                "thread_id": job.thread_id,
                "contract": job.contract,
            }
            if ledger is not None:
                age = ledger.heartbeat_age_s(job.job_id)
                if age is not None:
                    entry["heartbeat_age_s"] = age
            rows.append(entry)
        return rows

    def waiter_starvation(self) -> dict[str, Any]:
        """Oldest serial waiter age + amber bit (R2′ harm gate)."""
        from services.git_integration_worker.cursor_auto.waiter_visibility import (
            waiter_starvation_from_memory,
        )

        with self._lock:
            return waiter_starvation_from_memory(self._order, self._jobs)

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

    def _thread_lane_counts_unlocked(
        self,
        thread_id: str,
        *,
        exclude_job_id: str | None = None,
    ) -> dict[str, int]:
        """Peer pending/claimed on *thread_id*; caller must hold ``self._lock``."""
        pending = 0
        claimed = 0
        for jid in self._order:
            if exclude_job_id is not None and jid == exclude_job_id:
                continue
            job = self._jobs[jid]
            if job.thread_id != thread_id:
                continue
            if job.status == "queued":
                pending += 1
            elif job.status == "claimed" and not job.nested_sdk_finished:
                claimed += 1
        return {
            "same_thread_pending": pending,
            "same_thread_claimed": claimed,
        }

    def thread_lane_counts(
        self,
        thread_id: str,
        *,
        exclude_job_id: str | None = None,
    ) -> dict[str, int]:
        """Thread-scoped pending/claimed peers (same lock as :meth:`snapshot`).

        When durable, counts come from the job ledger — the same persisted
        source as the keyed job-state / ``thread_get`` observer path. In-memory
        queues (tests with ``durable=False``) keep the process-local count.
        Excludes *exclude_job_id* so an admit response can report *other*
        jobs on the lane — alone → 0; N queued predecessors → N. Claimed
        peers match supersede candidacy (``nested_sdk_finished`` /
        post-nested ``relay_phase`` excluded).
        """
        ledger = self._ledger_client()
        if ledger is not None:
            return ledger.thread_lane_counts(thread_id, exclude_job_id=exclude_job_id)
        with self._lock:
            return self._thread_lane_counts_unlocked(
                thread_id, exclude_job_id=exclude_job_id
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snap = {
                "pending": sum(1 for j in self._jobs.values() if j.status == "queued"),
                "claimed": sum(1 for j in self._jobs.values() if j.status == "claimed"),
                "done": sum(1 for j in self._jobs.values() if j.status == "done"),
                "failed": sum(1 for j in self._jobs.values() if j.status == "failed"),
                "report_undelivered": sum(
                    1 for j in self._jobs.values() if j.status == "report_undelivered"
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
            with ledger._connect() as conn:
                from services.git_integration_worker.cursor_auto.waiter_visibility import (
                    waiter_starvation_from_conn,
                )

                snap.update(waiter_starvation_from_conn(conn))
        else:
            from services.git_integration_worker.cursor_auto.waiter_visibility import (
                waiter_starvation_from_memory,
            )

            with self._lock:
                snap.update(waiter_starvation_from_memory(self._order, self._jobs))
        return snap

    def waiter_receipt(self, job_id: str) -> dict[str, Any]:
        """FIFO position + queued age for an enqueue receipt (this job)."""
        ledger = self._ledger_client()
        if ledger is not None:
            from services.git_integration_worker.cursor_auto.waiter_visibility import (
                waiter_fields_from_conn,
            )

            with ledger._connect() as conn:
                return waiter_fields_from_conn(conn, job_id)
        from services.git_integration_worker.cursor_auto.waiter_visibility import (
            waiter_fields_from_memory,
        )

        with self._lock:
            return waiter_fields_from_memory(self._order, self._jobs, job_id)


_QUEUE = AutoJobQueue(durable=True)
_drain_claim_gate: Any = None


def set_drain_claim_gate(probe: Any) -> None:
    """Install the drain latch used by ``claim_job`` (not ``claim_next``)."""
    global _drain_claim_gate
    _drain_claim_gate = probe


def drain_claim_gate_blocks() -> bool:
    """True when GIW is draining and ``claim_job`` must refuse."""
    probe = _drain_claim_gate
    return bool(probe is not None and probe())


def get_queue() -> AutoJobQueue:
    """Return the process-global Auto job queue used by enqueue and workers."""
    return _QUEUE


def reset_queue_for_tests(*, durable: bool = True) -> AutoJobQueue:
    """Replace the process-global queue (hermetic tests only)."""
    global _QUEUE, _drain_claim_gate
    _drain_claim_gate = None
    _QUEUE = AutoJobQueue(durable=durable)
    return _QUEUE
