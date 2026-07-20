"""D7 admission-time execution context + D4 review-child spawn state."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

SpawnState = Literal["pending", "final"]
SpawnProvenance = Literal["generate_review_child"]


def is_generate_review_child_lane_wired() -> bool:
    return True

_DDL = """
CREATE TABLE IF NOT EXISTS generate_admission_context (
    execution_id              TEXT PRIMARY KEY,
    auto_review_child         INTEGER NOT NULL,
    op                        TEXT NOT NULL,
    role                      TEXT NOT NULL,
    resolved_model              TEXT NOT NULL,
    parent_dispatch_thread_id TEXT,
    dispatch_thread_id        TEXT,
    spawn_template_provenance TEXT,
    review_surface            TEXT,
    dispatch_lane             TEXT,
    suppress_review_spawn     INTEGER,
    created_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_generate_admission_context_created
    ON generate_admission_context(created_at);

CREATE TABLE IF NOT EXISTS review_child_spawn_state (
    parent_execution_id       TEXT PRIMARY KEY,
    state                     TEXT NOT NULL CHECK (state IN ('pending', 'final')),
    review_child_execution_id TEXT,
    parent_dispatch_thread_id TEXT,
    parent_thread_id          TEXT,
    reviewer_model            TEXT,
    attempt_metadata          TEXT NOT NULL DEFAULT '{}',
    pending_at                TEXT NOT NULL,
    final_at                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_child_spawn_pending
    ON review_child_spawn_state(state) WHERE state = 'pending';
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_path() -> Path:
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "stargate-generate-admission.db"


_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("review_surface", "TEXT"),
    ("dispatch_lane", "TEXT"),
    ("suppress_review_spawn", "INTEGER"),
)


def _ensure_migration_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(generate_admission_context)")
    }
    for name, col_type in _MIGRATION_COLUMNS:
        if name not in existing:
            conn.execute(
                f"ALTER TABLE generate_admission_context ADD COLUMN {name} {col_type}"
            )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)
    _ensure_migration_columns(conn)
    return conn


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    execution_id: str
    auto_review_child: bool
    op: str
    role: str
    resolved_model: str
    parent_dispatch_thread_id: str | None
    dispatch_thread_id: str | None
    spawn_template_provenance: SpawnProvenance | None
    review_surface: str | None = None
    dispatch_lane: str | None = None
    suppress_review_spawn: bool = False
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class SpawnStateRow:
    parent_execution_id: str
    state: SpawnState
    review_child_execution_id: str | None
    parent_dispatch_thread_id: str | None
    parent_thread_id: str | None
    reviewer_model: str | None
    attempt_metadata: str
    pending_at: str
    final_at: str | None


def write_admission_context(
    *,
    execution_id: str,
    auto_review_child: bool,
    op: str,
    role: str,
    resolved_model: str,
    parent_dispatch_thread_id: str | None,
    dispatch_thread_id: str | None,
    spawn_template_provenance: SpawnProvenance | None = None,
    review_surface: str | None = None,
    dispatch_lane: str | None = None,
    suppress_review_spawn: bool = False,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO generate_admission_context "
            "(execution_id, auto_review_child, op, role, resolved_model, "
            "parent_dispatch_thread_id, dispatch_thread_id, spawn_template_provenance, "
            "review_surface, dispatch_lane, suppress_review_spawn, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                execution_id,
                int(auto_review_child),
                op,
                role,
                resolved_model,
                parent_dispatch_thread_id,
                dispatch_thread_id,
                spawn_template_provenance,
                review_surface,
                dispatch_lane,
                int(suppress_review_spawn),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def read_admission_context(execution_id: str) -> AdmissionContext | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT execution_id, auto_review_child, op, role, resolved_model, "
            "parent_dispatch_thread_id, dispatch_thread_id, spawn_template_provenance, "
            "review_surface, dispatch_lane, suppress_review_spawn, "
            "created_at FROM generate_admission_context WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    provenance = row["spawn_template_provenance"]
    suppress_raw = row["suppress_review_spawn"]
    return AdmissionContext(
        execution_id=row["execution_id"],
        auto_review_child=bool(row["auto_review_child"]),
        op=row["op"],
        role=row["role"],
        resolved_model=row["resolved_model"],
        parent_dispatch_thread_id=row["parent_dispatch_thread_id"],
        dispatch_thread_id=row["dispatch_thread_id"],
        spawn_template_provenance=provenance if provenance else None,
        review_surface=row["review_surface"],
        dispatch_lane=row["dispatch_lane"],
        suppress_review_spawn=bool(suppress_raw) if suppress_raw is not None else False,
        created_at=row["created_at"],
    )


def delete_admission_context(execution_id: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "DELETE FROM generate_admission_context WHERE execution_id=?",
            (execution_id,),
        )
        conn.commit()
    finally:
        conn.close()


def try_claim_spawn_pending(
    *,
    parent_execution_id: str,
    parent_dispatch_thread_id: str | None,
    parent_thread_id: str | None,
    reviewer_model: str,
    attempt_metadata: str = "{}",
) -> bool:
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT state FROM review_child_spawn_state WHERE parent_execution_id=?",
            (parent_execution_id,),
        ).fetchone()
        if existing is not None:
            return False
        conn.execute(
            "INSERT INTO review_child_spawn_state "
            "(parent_execution_id, state, parent_dispatch_thread_id, parent_thread_id, "
            "reviewer_model, attempt_metadata, pending_at) "
            "VALUES (?, 'pending', ?, ?, ?, ?, ?)",
            (
                parent_execution_id,
                parent_dispatch_thread_id,
                parent_thread_id,
                reviewer_model,
                attempt_metadata,
                _now(),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def finalize_spawn_state(
    *,
    parent_execution_id: str,
    review_child_execution_id: str,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE review_child_spawn_state SET state='final', "
            "review_child_execution_id=?, final_at=? "
            "WHERE parent_execution_id=?",
            (review_child_execution_id, _now(), parent_execution_id),
        )
        conn.commit()
    finally:
        conn.close()


def read_spawn_state(parent_execution_id: str) -> SpawnStateRow | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT parent_execution_id, state, review_child_execution_id, "
            "parent_dispatch_thread_id, parent_thread_id, reviewer_model, "
            "attempt_metadata, pending_at, final_at "
            "FROM review_child_spawn_state WHERE parent_execution_id=?",
            (parent_execution_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return SpawnStateRow(
        parent_execution_id=row["parent_execution_id"],
        state=row["state"],  # type: ignore[arg-type]
        review_child_execution_id=row["review_child_execution_id"],
        parent_dispatch_thread_id=row["parent_dispatch_thread_id"],
        parent_thread_id=row["parent_thread_id"],
        reviewer_model=row["reviewer_model"],
        attempt_metadata=row["attempt_metadata"],
        pending_at=row["pending_at"],
        final_at=row["final_at"],
    )


def list_pending_spawn_states() -> list[SpawnStateRow]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT parent_execution_id, state, review_child_execution_id, "
            "parent_dispatch_thread_id, parent_thread_id, reviewer_model, "
            "attempt_metadata, pending_at, final_at "
            "FROM review_child_spawn_state WHERE state='pending'"
        ).fetchall()
    finally:
        conn.close()
    return [
        SpawnStateRow(
            parent_execution_id=row["parent_execution_id"],
            state=row["state"],  # type: ignore[arg-type]
            review_child_execution_id=row["review_child_execution_id"],
            parent_dispatch_thread_id=row["parent_dispatch_thread_id"],
            parent_thread_id=row["parent_thread_id"],
            reviewer_model=row["reviewer_model"],
            attempt_metadata=row["attempt_metadata"],
            pending_at=row["pending_at"],
            final_at=row["final_at"],
        )
        for row in rows
    ]


def reset_generate_admission_stores_for_tests() -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM generate_admission_context")
        conn.execute("DELETE FROM review_child_spawn_state")
        conn.commit()
    finally:
        conn.close()
