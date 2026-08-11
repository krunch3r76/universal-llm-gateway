"""Durable SQLite ledger for ``lane:cursor-auto`` jobs (queue-owner status)."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.job_lifecycle import (
    PHASE_ADMITTED,
    PHASE_BOUND,
    PHASE_CLAIMED_PRE_ADMIT,
    PHASE_QUEUED,
    query_observer_state,
    query_thread_lane_counts,
    terminal_phase_for_status,
)
from services.git_integration_worker.cursor_auto.job_record import (
    job_from_row,
    job_record,
)
from services.git_integration_worker.cursor_dispatch_ledger import (
    _connect,
    _ledger_path,
)

if TYPE_CHECKING:
    from services.git_integration_worker.cursor_auto.queue import AutoJob

logger = get_logger(__name__)

TERMINAL_REASON_QUEUE_OWNER_RESTART = "queue_owner_restart"

RELAY_PHASE_NONE = "none"
RELAY_PHASE_DISPATCHED = "dispatched"
RELAY_PHASE_SDK_TERMINAL = "sdk_terminal"
RELAY_PHASE_CLOSEOUT_POSTED = "closeout_posted"

_OPEN_STATUSES = ("queued", "claimed")
_TERMINAL_STATUSES = ("done", "failed", "report_undelivered", "superseded")

_DDL = """
CREATE TABLE IF NOT EXISTS cursor_auto_jobs (
    job_id            TEXT PRIMARY KEY,
    request_id        TEXT,
    thread_id         TEXT NOT NULL,
    turn_number       INTEGER,
    status            TEXT NOT NULL CHECK (status IN (
                          'queued','claimed','done','failed',
                          'report_undelivered','superseded')),
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


class AutoJobLedger:
    """Durable owner of cursor-auto job status; survives GIW restart."""

    _instance: AutoJobLedger | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._db_path = _ledger_path()
        with self._connect() as conn:
            conn.executescript(_DDL)
            self._ensure_relay_columns(conn)
            self._ensure_report_undelivered_status(conn)

    @staticmethod
    def _ensure_report_undelivered_status(conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='cursor_auto_jobs'"
        ).fetchone()
        if row is None or row["sql"] is None:
            return
        if "'report_undelivered'" in row["sql"]:
            return
        conn.executescript(
            """
            CREATE TABLE cursor_auto_jobs_v2 (
                job_id            TEXT PRIMARY KEY,
                request_id        TEXT,
                thread_id         TEXT NOT NULL,
                turn_number       INTEGER,
                status            TEXT NOT NULL CHECK (status IN (
                                      'queued','claimed','done','failed',
                                      'report_undelivered','superseded')),
                enqueued_at       TEXT NOT NULL,
                claimed_at        TEXT,
                last_heartbeat_at TEXT,
                ended_at          TEXT,
                terminal_reason   TEXT,
                record_json       TEXT NOT NULL,
                dispatch_id       TEXT,
                relay_phase       TEXT DEFAULT 'none',
                admitted_at       TEXT,
                bound_at          TEXT,
                lifecycle_phase   TEXT DEFAULT 'queued'
            );
            INSERT INTO cursor_auto_jobs_v2 (
                job_id, request_id, thread_id, turn_number, status,
                enqueued_at, claimed_at, last_heartbeat_at, ended_at,
                terminal_reason, record_json, dispatch_id, relay_phase,
                admitted_at, bound_at, lifecycle_phase
            )
            SELECT
                job_id, request_id, thread_id, turn_number, status,
                enqueued_at, claimed_at, last_heartbeat_at, ended_at,
                terminal_reason, record_json, dispatch_id, relay_phase,
                admitted_at, bound_at, lifecycle_phase
            FROM cursor_auto_jobs;
            DROP TABLE cursor_auto_jobs;
            ALTER TABLE cursor_auto_jobs_v2 RENAME TO cursor_auto_jobs;
            CREATE INDEX IF NOT EXISTS idx_auto_jobs_status
                ON cursor_auto_jobs (status);
            CREATE INDEX IF NOT EXISTS idx_auto_jobs_thread
                ON cursor_auto_jobs (thread_id);
            CREATE INDEX IF NOT EXISTS idx_auto_jobs_dispatch
                ON cursor_auto_jobs (dispatch_id);
            """
        )

    @staticmethod
    def _ensure_relay_columns(conn: sqlite3.Connection) -> None:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(cursor_auto_jobs)")}
        if "dispatch_id" not in cols:
            conn.execute("ALTER TABLE cursor_auto_jobs ADD COLUMN dispatch_id TEXT")
        if "relay_phase" not in cols:
            conn.execute(
                "ALTER TABLE cursor_auto_jobs ADD COLUMN relay_phase TEXT "
                "DEFAULT 'none'"
            )
        if "admitted_at" not in cols:
            conn.execute("ALTER TABLE cursor_auto_jobs ADD COLUMN admitted_at TEXT")
        if "bound_at" not in cols:
            conn.execute("ALTER TABLE cursor_auto_jobs ADD COLUMN bound_at TEXT")
        if "lifecycle_phase" not in cols:
            conn.execute(
                "ALTER TABLE cursor_auto_jobs ADD COLUMN lifecycle_phase TEXT "
                f"DEFAULT '{PHASE_QUEUED}'"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auto_jobs_dispatch "
            "ON cursor_auto_jobs (dispatch_id)"
        )

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
        payload = json.dumps(job_record(job), sort_keys=True, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cursor_auto_jobs "
                "(job_id, request_id, thread_id, turn_number, status, enqueued_at, "
                "lifecycle_phase, record_json) VALUES (?,?,?,?,?,?,?,?)",
                (
                    job.job_id,
                    job.request_id,
                    job.thread_id,
                    job.turn_number,
                    job.status,
                    _now_iso(),
                    PHASE_QUEUED,
                    payload,
                ),
            )

    def sync_record(self, job: AutoJob) -> None:
        payload = json.dumps(job_record(job), sort_keys=True, separators=(",", ":"))
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
                "last_heartbeat_at=?, lifecycle_phase=? "
                "WHERE job_id=? AND status='queued'",
                (now, now, PHASE_CLAIMED_PRE_ADMIT, job_id),
            )

    def mark_admitted(self, job_id: str) -> None:
        """Stamp admit clock after a successful bus ``status:admitted`` reply."""
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_jobs SET admitted_at=?, lifecycle_phase=? "
                "WHERE job_id=? AND status='claimed' AND admitted_at IS NULL",
                (now, PHASE_ADMITTED, job_id),
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
                "terminal_reason=?, lifecycle_phase=? WHERE job_id=?",
                (
                    status,
                    now,
                    terminal_reason,
                    terminal_phase_for_status(status),
                    job_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM cursor_auto_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        return job_from_row(row) if row is not None else None

    def list_open(self) -> list[AutoJob]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cursor_auto_jobs WHERE status IN ('queued','claimed') "
                "ORDER BY enqueued_at ASC"
            ).fetchall()
        return [job_from_row(row) for row in rows]

    def bind_dispatch(
        self,
        job_id: str,
        *,
        dispatch_id: str,
        relay_phase: str = RELAY_PHASE_DISPATCHED,
    ) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_jobs SET dispatch_id=?, relay_phase=?, "
                "bound_at=COALESCE(bound_at, ?), lifecycle_phase=? WHERE job_id=?",
                (dispatch_id, relay_phase, now, PHASE_BOUND, job_id),
            )

    def set_relay_phase(self, job_id: str, *, relay_phase: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_jobs SET relay_phase=? WHERE job_id=?",
                (relay_phase, job_id),
            )

    def read_relay_state(self, job_id: str) -> dict[str, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT dispatch_id, relay_phase, status FROM cursor_auto_jobs "
                "WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            return {"dispatch_id": None, "relay_phase": None, "status": None}
        return {
            "dispatch_id": row["dispatch_id"],
            "relay_phase": row["relay_phase"] or RELAY_PHASE_NONE,
            "status": row["status"],
        }

    def read_record_json(self, job_id: str) -> dict[str, Any]:
        """Return decoded ``record_json`` for *job_id*, or ``{}`` if missing."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM cursor_auto_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        if row is None:
            return {}
        data = json.loads(row["record_json"] or "{}")
        return data if isinstance(data, dict) else {}

    def merge_record_json(self, job_id: str, patch: dict[str, Any]) -> None:
        """Merge *patch* into durable ``record_json`` (silence-family marks)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_json FROM cursor_auto_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if row is None:
                return
            data = json.loads(row["record_json"] or "{}")
            if not isinstance(data, dict):
                data = {}
            data.update(patch)
            conn.execute(
                "UPDATE cursor_auto_jobs SET record_json=? WHERE job_id=?",
                (
                    json.dumps(data, sort_keys=True, separators=(",", ":")),
                    job_id,
                ),
            )

    def get_by_dispatch_id(self, dispatch_id: str) -> AutoJob | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cursor_auto_jobs WHERE dispatch_id=? LIMIT 1",
                (dispatch_id,),
            ).fetchone()
        return job_from_row(row) if row is not None else None

    def observer_state(
        self,
        *,
        job_id: str | None = None,
        thread_id: str | None = None,
        include_terminal: bool = False,
    ) -> dict[str, Any] | None:
        """Return the codeblind observer view for a job or newest lane job."""
        with self._connect() as conn:
            return query_observer_state(
                conn,
                job_id=job_id,
                thread_id=thread_id,
                include_terminal=include_terminal,
            )

    def thread_lane_counts(
        self,
        thread_id: str,
        *,
        exclude_job_id: str | None = None,
    ) -> dict[str, int]:
        """Persisted same-thread pending/claimed peers (enqueue + read SoT)."""
        with self._connect() as conn:
            return query_thread_lane_counts(
                conn, thread_id, exclude_job_id=exclude_job_id
            )

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
