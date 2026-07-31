"""Durable work-key registry — charter_window_work_key (spec §2.1)."""

from __future__ import annotations

import time

from libs.charter_runner_store.db import execute_with_retry

from .work_key import WorkKeyRecord


def _row_to_record(row) -> WorkKeyRecord:
    return WorkKeyRecord(
        work_key=str(row["work_key"]),
        root_id=str(row["root_id"]),
        window_id=str(row["window_id"]),
        dispatch_id=row["dispatch_id"],
        thread_id=row["thread_id"],
        admitted_at=float(row["admitted_at"]),
        disposition=row["disposition"],
    )


def record_admit(
    conn,
    *,
    work_key: str,
    root_id: str,
    window_id: str,
    dispatch_id: str | None = None,
    thread_id: str | None = None,
    admitted_at: float | None = None,
) -> None:
    """Insert or refresh an admit row (disposition cleared on re-stamp)."""
    ts = float(admitted_at if admitted_at is not None else time.time())
    execute_with_retry(
        conn,
        """
        INSERT INTO charter_window_work_key (
          work_key, root_id, window_id, dispatch_id, thread_id, admitted_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(work_key, window_id) DO UPDATE SET
          dispatch_id=excluded.dispatch_id,
          thread_id=excluded.thread_id,
          admitted_at=excluded.admitted_at
        WHERE charter_window_work_key.disposition IS NULL
        """,
        (work_key, root_id, window_id, dispatch_id, thread_id, ts),
    )


def live_undispositioned_for_key(conn, work_key: str) -> list[WorkKeyRecord]:
    """Rows for ``work_key`` with no disposition stamp yet."""
    rows = conn.execute(
        """
        SELECT work_key, root_id, window_id, dispatch_id, thread_id,
               admitted_at, disposition
          FROM charter_window_work_key
         WHERE work_key = ? AND disposition IS NULL
        """,
        (work_key,),
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def harvested_for_key(conn, work_key: str) -> list[WorkKeyRecord]:
    """Rows for ``work_key`` with disposition='harvested'."""
    rows = conn.execute(
        """
        SELECT work_key, root_id, window_id, dispatch_id, thread_id,
               admitted_at, disposition
          FROM charter_window_work_key
         WHERE work_key = ? AND disposition = 'harvested'
        ORDER BY admitted_at DESC
        """,
        (work_key,),
    ).fetchall()
    return [_row_to_record(row) for row in rows]


def stamp_disposition(
    conn,
    *,
    work_key: str,
    window_id: str,
    disposition: str,
    disposition_at: float | None = None,
) -> bool:
    """Stamp ``harvested`` / ``superseded`` / ``failed`` / ``abandoned``; return updated."""
    ts = float(disposition_at if disposition_at is not None else time.time())
    cur = execute_with_retry(
        conn,
        """
        UPDATE charter_window_work_key
           SET disposition = ?, disposition_at = ?
         WHERE work_key = ? AND window_id = ? AND disposition IS NULL
        """,
        (disposition, ts, work_key, window_id),
    )
    return cur.rowcount > 0


def find_record_by_window_id(conn, window_id: str) -> WorkKeyRecord | None:
    row = conn.execute(
        """
        SELECT work_key, root_id, window_id, dispatch_id, thread_id,
               admitted_at, disposition
          FROM charter_window_work_key
         WHERE window_id = ?
         ORDER BY admitted_at DESC
         LIMIT 1
        """,
        (window_id,),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def find_record(
    conn,
    *,
    work_key: str,
    window_id: str,
) -> WorkKeyRecord | None:
    row = conn.execute(
        """
        SELECT work_key, root_id, window_id, dispatch_id, thread_id,
               admitted_at, disposition
          FROM charter_window_work_key
         WHERE work_key = ? AND window_id = ?
        """,
        (work_key, window_id),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


__all__ = [
    "find_record",
    "find_record_by_window_id",
    "harvested_for_key",
    "live_undispositioned_for_key",
    "record_admit",
    "stamp_disposition",
]
