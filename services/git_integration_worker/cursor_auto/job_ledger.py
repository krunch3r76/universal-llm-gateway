"""Durable SQLite ledger for ``lane:cursor-auto`` jobs (queue-owner status)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_dispatch_ledger import (
    _connect,
    _ledger_path,
)

if TYPE_CHECKING:
    from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

TERMINAL_REASON_QUEUE_OWNER_RESTART = "queue_owner_restart"

_OPEN_STATUSES = ("queued", "claimed")
_TERMINAL_STATUSES = ("done", "failed", "superseded")

_DDL = """
CREATE TABLE IF NOT EXISTS cursor_auto_jobs (
    job_id            TEXT PRIMARY KEY,
    request_id        TEXT,
    thread_id         TEXT NOT NULL,
    turn_number       INTEGER,
    status            TEXT NOT NULL CHECK (status IN (
                          'queued','claimed','done','failed','superseded')),
    enqueued_at       TEXT NOT NULL,
    claimed_at        TEXT,
    last_heartbeat_at TEXT,
    ended_at          TEXT,
    terminal_reason   TEXT,
    record_json       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auto_jobs_status ON cursor_auto_jobs (status);
CREATE INDEX IF NOT EXISTS idx_auto_jobs_thread ON cursor_auto_jobs (thread_id);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _job_record(job: AutoJob) -> dict[str, Any]:  # noqa: F821
    return {
        "job_id": job.job_id,
        "thread_id": job.thread_id,
        "turn_number": job.turn_number,
        "subject": job.subject,
        "body": job.body,
        "from_agent": job.from_agent,
        "to_agent": job.to_agent,
        "desired_model": job.desired_model,
        "desired_effort": job.desired_effort,
        "contract": job.contract,
        "require_attended": job.require_attended,
        "request_id": job.request_id,
        "enqueued_at_mono": job.enqueued_at,
        "superseded_by": job.superseded_by,
        "supersedes": job.supersedes,
        "superseded_dispatch_id": job.superseded_dispatch_id,
    }


def _job_from_row(row: sqlite3.Row) -> AutoJob:  # noqa: F821
    from services.git_integration_worker.cursor_auto.queue import AutoJob

    data = json.loads(row["record_json"] or "{}")
    if not isinstance(data, dict):
        data = {}
    return AutoJob(
        job_id=row["job_id"],
        thread_id=row["thread_id"],
        turn_number=int(row["turn_number"] or data.get("turn_number") or 0),
        subject=str(data.get("subject") or ""),
        body=str(data.get("body") or ""),
        from_agent=str(data.get("from_agent") or ""),
        to_agent=str(data.get("to_agent") or "cursor"),
        desired_model=str(data.get("desired_model") or "auto"),
        desired_effort=str(data.get("desired_effort") or "medium"),
        contract=str(data.get("contract") or "answer"),
        require_attended=bool(data.get("require_attended", False)),
        request_id=row["request_id"] or data.get("request_id"),
        enqueued_at=float(data.get("enqueued_at_mono") or 0.0),
        status=row["status"],
        superseded_by=data.get("superseded_by"),
        supersedes=data.get("supersedes"),
        superseded_dispatch_id=data.get("superseded_dispatch_id"),
    )


class AutoJobLedger:
    """Durable owner of cursor-auto job status; survives GIW restart."""

    _instance: AutoJobLedger | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._db_path = _ledger_path()
        with self._connect() as conn:
            conn.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    @classmethod
    def instance(cls) -> AutoJobLedger:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._instance = None

    def insert(self, job: AutoJob) -> None:
        payload = json.dumps(_job_record(job), sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cursor_auto_jobs "
                "(job_id, request_id, thread_id, turn_number, status, enqueued_at, "
                "record_json) VALUES (?,?,?,?,?,?,?)",
                (
                    job.job_id,
                    job.request_id,
                    job.thread_id,
                    job.turn_number,
                    job.status,
                    _now_iso(),
                    payload,
                ),
            )

    def sync_record(self, job: AutoJob) -> None:
        payload = json.dumps(_job_record(job), sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_jobs SET status=?, record_json=? WHERE job_id=?",
                (job.status, payload, job.job_id),
            )

    def mark_claimed(self, job_id: str) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_jobs SET status='claimed', claimed_at=?, "
                "last_heartbeat_at=? WHERE job_id=? AND status='queued'",
                (now, now, job_id),
            )

    def bump_heartbeat(self, job_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_jobs SET last_heartbeat_at=? "
                "WHERE job_id=? AND status='claimed'",
                (_now_iso(), job_id),
            )

    def mark_terminal(
        self,
        job_id: str,
        *,
        status: str,
        terminal_reason: str | None = None,
    ) -> AutoJob | None:
        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"invalid terminal status: {status}")
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cursor_auto_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None or row["status"] not in _OPEN_STATUSES:
                return None
            conn.execute(
                "UPDATE cursor_auto_jobs SET status=?, ended_at=?, "
                "terminal_reason=? WHERE job_id=?",
                (status, now, terminal_reason, job_id),
            )
            row = conn.execute(
                "SELECT * FROM cursor_auto_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return _job_from_row(row) if row is not None else None

    def list_open(self) -> list[AutoJob]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cursor_auto_jobs WHERE status IN ('queued','claimed') "
                "ORDER BY enqueued_at ASC"
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, terminal_reason, COUNT(*) AS n "
                "FROM cursor_auto_jobs GROUP BY status, terminal_reason"
            ).fetchall()
        counts: dict[str, int] = {}
        failed_on_restart = 0
        for row in rows:
            key = str(row["status"])
            counts[key] = counts.get(key, 0) + int(row["n"])
            if (
                key == "failed"
                and row["terminal_reason"] == TERMINAL_REASON_QUEUE_OWNER_RESTART
            ):
                failed_on_restart += int(row["n"])
        counts["failed_on_restart"] = failed_on_restart
        counts["total"] = sum(
            int(row["n"]) for row in rows if row["status"] is not None
        )
        return counts


def get_ledger() -> AutoJobLedger:
    """Return the process-global durable Auto job ledger."""
    return AutoJobLedger.instance()
