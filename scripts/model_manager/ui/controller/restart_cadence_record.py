"""SQLite audit rows for restart-cadence sync_restart arms.

Each row ties a deferred or completed non-force ``sync_restart`` to the master
``code_ref`` that motivated it. Manage's ``RestartCadenceLoop`` inserts on arm
and backfills ``new_pid`` when the intent fires; operators read quotable rows
via ``list_complete``. Uses the same database file as ``RestartIntentStore``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .restart_intent_store import RestartIntentStore
from .service_config import GATEWAY_DIR

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS restart_cadence_records (
    intent_id TEXT PRIMARY KEY,
    service TEXT NOT NULL,
    old_pid INTEGER,
    new_pid INTEGER,
    code_ref TEXT NOT NULL,
    armed_at TEXT NOT NULL,
    recorded_at TEXT
)
"""


def default_db_path() -> Path:
    """Return the shared restart-intents database path."""
    GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
    return GATEWAY_DIR / "restart-intents.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    db = path or default_db_path()
    conn = sqlite3.connect(db, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_CREATE_TABLE)
    return conn


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class CadenceRecord:
    """One restart-cadence arm row."""

    intent_id: str
    service: str
    old_pid: int | None
    new_pid: int | None
    code_ref: str
    armed_at: str
    recorded_at: str | None


def _row_to_record(row: sqlite3.Row) -> CadenceRecord:
    return CadenceRecord(
        intent_id=str(row["intent_id"]),
        service=str(row["service"]),
        old_pid=row["old_pid"],
        new_pid=row["new_pid"],
        code_ref=str(row["code_ref"]),
        armed_at=str(row["armed_at"]),
        recorded_at=row["recorded_at"],
    )


def insert_armed(
    *,
    intent_id: str,
    service: str,
    old_pid: int | None,
    code_ref: str,
    armed_at: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Insert a pending cadence row after ``execute`` returned a restart intent id."""
    stamped = armed_at or _now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO restart_cadence_records
                (intent_id, service, old_pid, new_pid, code_ref, armed_at, recorded_at)
            VALUES (?, ?, ?, NULL, ?, ?, NULL)
            """,
            (intent_id, service, old_pid, code_ref, stamped),
        )
        conn.commit()


def insert_completed(
    *,
    intent_id: str,
    service: str,
    old_pid: int | None,
    new_pid: int,
    code_ref: str,
    armed_at: str | None = None,
    recorded_at: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Insert a fully observed cadence row when restart finished in one call."""
    stamped_arm = armed_at or _now_iso()
    stamped_rec = recorded_at or _now_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO restart_cadence_records
                (intent_id, service, old_pid, new_pid, code_ref, armed_at, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (intent_id, service, old_pid, new_pid, code_ref, stamped_arm, stamped_rec),
        )
        conn.commit()


def fill_new_pid(
    intent_id: str,
    new_pid: int,
    *,
    recorded_at: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Set ``new_pid`` and ``recorded_at`` when the service process recycled."""
    stamped = recorded_at or _now_iso()
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            UPDATE restart_cadence_records
            SET new_pid=?, recorded_at=?
            WHERE intent_id=? AND new_pid IS NULL
            """,
            (new_pid, stamped, intent_id),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_record(intent_id: str, *, db_path: Path | None = None) -> bool:
    """Remove a cadence row when the linked intent ended without executing."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM restart_cadence_records WHERE intent_id=?",
            (intent_id,),
        )
        conn.commit()
        return cur.rowcount > 0


def list_pending_new_pid(*, db_path: Path | None = None) -> list[CadenceRecord]:
    """Return rows still waiting for a post-restart pid observation."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM restart_cadence_records WHERE new_pid IS NULL ORDER BY armed_at"
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def list_complete(*, db_path: Path | None = None) -> list[CadenceRecord]:
    """Return quotable rows with service, intent, both pids, and code_ref set."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM restart_cadence_records
            WHERE new_pid IS NOT NULL
              AND service IS NOT NULL
              AND intent_id IS NOT NULL
              AND code_ref IS NOT NULL
            ORDER BY recorded_at
            """
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def get_record(intent_id: str, *, db_path: Path | None = None) -> CadenceRecord | None:
    """Load one cadence row by intent id."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM restart_cadence_records WHERE intent_id=?",
            (intent_id,),
        ).fetchone()
    return _row_to_record(row) if row is not None else None


def load_intent(intent_id: str) -> Any:
    """Load the matching restart intent from the shared store."""
    return RestartIntentStore.instance().get(intent_id)


__all__ = [
    "CadenceRecord",
    "default_db_path",
    "delete_record",
    "fill_new_pid",
    "get_record",
    "insert_armed",
    "insert_completed",
    "list_complete",
    "list_pending_new_pid",
    "load_intent",
]
