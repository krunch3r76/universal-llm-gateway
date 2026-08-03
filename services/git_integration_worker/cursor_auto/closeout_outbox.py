"""Durable closeout envelope outbox for cursor-auto relay write-ahead."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from services.git_integration_worker.cursor_dispatch_ledger import (
    _connect,
    _ledger_path,
)

_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS cursor_auto_closeout_outbox (
    dispatch_id       TEXT PRIMARY KEY,
    job_id            TEXT NOT NULL,
    thread_id         TEXT NOT NULL,
    to_agent          TEXT NOT NULL,
    from_agent        TEXT NOT NULL,
    subject           TEXT NOT NULL,
    envelope_body     TEXT NOT NULL,
    envelope_sha256   TEXT NOT NULL,
    closeout_status   TEXT NOT NULL,
    closeout_source   TEXT,
    request_id        TEXT,
    request_turn      INTEGER NOT NULL,
    checkpoint_value  TEXT,
    tree_residue      INTEGER,
    worker_id         TEXT NOT NULL,
    worker_started_at TEXT NOT NULL,
    state             TEXT NOT NULL CHECK (state IN
                        ('pending','posted','posted_confirmed','discarded','abandoned')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    persisted_at      TEXT NOT NULL,
    posted_at         TEXT,
    discarded_reason  TEXT
);
CREATE INDEX IF NOT EXISTS idx_auto_outbox_state ON cursor_auto_closeout_outbox (state);
CREATE INDEX IF NOT EXISTS idx_auto_outbox_job   ON cursor_auto_closeout_outbox (job_id);
"""

_REPLAYABLE_STATES = ("pending", "posted")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def envelope_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OutboxRow:
    dispatch_id: str
    job_id: str
    thread_id: str
    to_agent: str
    from_agent: str
    subject: str
    envelope_body: str
    envelope_sha256: str
    closeout_status: str
    closeout_source: str | None
    request_id: str | None
    request_turn: int
    checkpoint_value: str | None
    tree_residue: int | None
    worker_id: str
    worker_started_at: str
    state: str
    attempts: int
    persisted_at: str
    posted_at: str | None
    discarded_reason: str | None


def _row_from_sqlite(row: sqlite3.Row) -> OutboxRow:
    return OutboxRow(
        dispatch_id=row["dispatch_id"],
        job_id=row["job_id"],
        thread_id=row["thread_id"],
        to_agent=row["to_agent"],
        from_agent=row["from_agent"],
        subject=row["subject"],
        envelope_body=row["envelope_body"],
        envelope_sha256=row["envelope_sha256"],
        closeout_status=row["closeout_status"],
        closeout_source=row["closeout_source"],
        request_id=row["request_id"],
        request_turn=int(row["request_turn"]),
        checkpoint_value=row["checkpoint_value"],
        tree_residue=row["tree_residue"],
        worker_id=row["worker_id"],
        worker_started_at=row["worker_started_at"],
        state=row["state"],
        attempts=int(row["attempts"]),
        persisted_at=row["persisted_at"],
        posted_at=row["posted_at"],
        discarded_reason=row["discarded_reason"],
    )


class CloseoutOutboxStore:
    """SQLite outbox for composed CLOSEOUT envelopes awaiting bus delivery."""

    _instance: CloseoutOutboxStore | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._db_path = _ledger_path()
        with self._connect() as conn:
            conn.executescript(_OUTBOX_DDL)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    @classmethod
    def instance(cls) -> CloseoutOutboxStore:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._instance = None

    def persist_pending(
        self,
        *,
        dispatch_id: str,
        job_id: str,
        thread_id: str,
        to_agent: str,
        from_agent: str,
        subject: str,
        envelope_body: str,
        closeout_status: str,
        request_turn: int,
        worker_id: str,
        worker_started_at: str,
        closeout_source: str | None = None,
        request_id: str | None = None,
        checkpoint_value: str | None = None,
        tree_residue: int | None = None,
    ) -> OutboxRow:
        sha = envelope_sha256(envelope_body)
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cursor_auto_closeout_outbox "
                "(dispatch_id, job_id, thread_id, to_agent, from_agent, subject, "
                "envelope_body, envelope_sha256, closeout_status, closeout_source, "
                "request_id, request_turn, checkpoint_value, tree_residue, worker_id, "
                "worker_started_at, state, attempts, persisted_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'pending', 0, ?)",
                (
                    dispatch_id,
                    job_id,
                    thread_id,
                    to_agent,
                    from_agent,
                    subject,
                    envelope_body,
                    sha,
                    closeout_status,
                    closeout_source,
                    request_id,
                    request_turn,
                    checkpoint_value,
                    tree_residue,
                    worker_id,
                    worker_started_at,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM cursor_auto_closeout_outbox WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        assert row is not None
        return _row_from_sqlite(row)

    def mark_posted(self, dispatch_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_closeout_outbox SET state='posted', posted_at=? "
                "WHERE dispatch_id=? AND state IN ('pending','posted')",
                (_now_iso(), dispatch_id),
            )

    def mark_confirmed(self, dispatch_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_closeout_outbox SET state='posted_confirmed', "
                "posted_at=COALESCE(posted_at, ?) WHERE dispatch_id=?",
                (_now_iso(), dispatch_id),
            )

    def discard(self, dispatch_id: str, *, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_closeout_outbox SET state='discarded', "
                "discarded_reason=? WHERE dispatch_id=?",
                (reason, dispatch_id),
            )

    def abandon(self, dispatch_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_closeout_outbox SET state='abandoned' "
                "WHERE dispatch_id=?",
                (dispatch_id,),
            )

    def increment_attempts(self, dispatch_id: str) -> int:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_auto_closeout_outbox SET attempts=attempts+1 "
                "WHERE dispatch_id=?",
                (dispatch_id,),
            )
            row = conn.execute(
                "SELECT attempts FROM cursor_auto_closeout_outbox WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        return int(row["attempts"]) if row is not None else 0

    def get(self, dispatch_id: str) -> OutboxRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cursor_auto_closeout_outbox WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        return _row_from_sqlite(row) if row is not None else None

    def list_replayable(self, *, exclude_worker_id: str) -> list[OutboxRow]:
        placeholders = ",".join("?" for _ in _REPLAYABLE_STATES)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM cursor_auto_closeout_outbox "
                f"WHERE state IN ({placeholders}) AND worker_id != ? "
                f"ORDER BY persisted_at ASC",
                (*_REPLAYABLE_STATES, exclude_worker_id),
            ).fetchall()
        return [_row_from_sqlite(row) for row in rows]

    def has_delivered_for_job(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM cursor_auto_closeout_outbox "
                "WHERE job_id=? AND state IN ('posted','posted_confirmed') LIMIT 1",
                (job_id,),
            ).fetchone()
        return row is not None

    def has_pending_for_job(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM cursor_auto_closeout_outbox "
                "WHERE job_id=? AND state='pending' LIMIT 1",
                (job_id,),
            ).fetchone()
        return row is not None


def get_outbox_store() -> CloseoutOutboxStore:
    return CloseoutOutboxStore.instance()
