"""SQLite durable in-flight ledger for CDP generate admit/finalize claims."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .skill_suggest_durable_state import query_event_service

CLAIM_LEASE_S = 30.0

_DDL = """
CREATE TABLE IF NOT EXISTS cdp_inflight_leg (
    execution_id              TEXT PRIMARY KEY,
    request_id                TEXT NOT NULL,
    satellite_execution_id    TEXT,
    thread_id                 TEXT NOT NULL,
    pointer_turn              INTEGER NOT NULL DEFAULT 1,
    caller_agent              TEXT,
    prompt_uri                TEXT NOT NULL,
    model_id                  TEXT NOT NULL,
    max_wall_s                REAL NOT NULL,
    admitted_at               TEXT NOT NULL,
    proof_emitted             INTEGER NOT NULL DEFAULT 0,
    delivered                 INTEGER NOT NULL DEFAULT 0,
    finalize_claim_until      TEXT,
    finalize_claim_holder     TEXT,
    abandoned                 INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cdp_inflight_open
    ON cdp_inflight_leg(proof_emitted) WHERE proof_emitted=0 AND abandoned=0;
"""

_LEG_SELECT = (
    "SELECT execution_id, request_id, satellite_execution_id, thread_id, "
    "pointer_turn, caller_agent, prompt_uri, model_id, max_wall_s, admitted_at, "
    "proof_emitted, delivered, abandoned FROM cdp_inflight_leg"
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _db_path() -> Path:
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "stargate-cdp-generate-inflight.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_DDL)
    return conn


@dataclass(frozen=True, slots=True)
class InflightLeg:
    execution_id: str
    request_id: str
    satellite_execution_id: str | None
    thread_id: str
    pointer_turn: int
    caller_agent: str | None
    prompt_uri: str
    model_id: str
    max_wall_s: float
    admitted_at: str
    proof_emitted: bool
    delivered: bool
    abandoned: bool


def _row_to_leg(row: sqlite3.Row) -> InflightLeg:
    return InflightLeg(
        execution_id=row["execution_id"],
        request_id=row["request_id"],
        satellite_execution_id=row["satellite_execution_id"],
        thread_id=row["thread_id"],
        pointer_turn=int(row["pointer_turn"]),
        caller_agent=row["caller_agent"],
        prompt_uri=row["prompt_uri"],
        model_id=row["model_id"],
        max_wall_s=float(row["max_wall_s"]),
        admitted_at=row["admitted_at"],
        proof_emitted=bool(row["proof_emitted"]),
        delivered=bool(row["delivered"]),
        abandoned=bool(row["abandoned"]),
    )


def upsert_inflight_leg(
    *,
    execution_id: str,
    request_id: str,
    thread_id: str,
    pointer_turn: int,
    caller_agent: str | None,
    prompt_uri: str,
    model_id: str,
    max_wall_s: float,
) -> None:
    """Persist admitted leg before worker task spawn (AC12)."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO cdp_inflight_leg "
            "(execution_id, request_id, thread_id, pointer_turn, caller_agent, "
            "prompt_uri, model_id, max_wall_s, admitted_at, proof_emitted, delivered, "
            "abandoned) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0)",
            (
                execution_id,
                request_id,
                thread_id,
                max(1, int(pointer_turn)),
                caller_agent,
                prompt_uri,
                model_id,
                float(max_wall_s),
                _now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def attach_satellite_execution_id(
    *, execution_id: str, satellite_execution_id: str
) -> None:
    """Record satellite submit correlation on the in-flight leg."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET satellite_execution_id=? WHERE execution_id=?",
            (satellite_execution_id, execution_id),
        )
        conn.commit()
    finally:
        conn.close()


def read_inflight_leg(execution_id: str) -> InflightLeg | None:
    """Load one durable in-flight leg row by Stargate ``execution_id``."""
    conn = _connect()
    try:
        row = conn.execute(
            f"{_LEG_SELECT} WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_leg(row) if row is not None else None


def list_open_inflight_legs() -> list[InflightLeg]:
    """Return legs lacking board-terminal proof and not yet abandoned."""
    conn = _connect()
    try:
        rows = conn.execute(
            f"{_LEG_SELECT} WHERE proof_emitted=0 AND abandoned=0"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_leg(row) for row in rows]


def mark_proof_emitted(execution_id: str) -> None:
    """Record that ``cdp.generate.proof`` or ``cdp.generate.stalled`` was published."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET proof_emitted=1, finalize_claim_until=NULL, "
            "finalize_claim_holder=NULL WHERE execution_id=?",
            (execution_id,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_delivered(execution_id: str) -> None:
    """Record successful on-behalf ``from=cdp`` bus delivery for the leg."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET delivered=1 WHERE execution_id=?",
            (execution_id,),
        )
        conn.commit()
    finally:
        conn.close()


def mark_abandoned(execution_id: str) -> None:
    """Mark leg abandoned after ``reconcile_abandoned`` stall (sweep ledger row)."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg SET abandoned=1 WHERE execution_id=?",
            (execution_id,),
        )
        conn.commit()
    finally:
        conn.close()


def try_claim_proof_publish(*, execution_id: str, holder: str) -> bool:
    """Atomic claim before board-terminal publish (AC5)."""
    now = _now()
    lease_until = (datetime.now(UTC) + timedelta(seconds=CLAIM_LEASE_S)).isoformat()
    conn = _connect()
    try:
        conn.execute(
            "UPDATE cdp_inflight_leg "
            "SET finalize_claim_until=?, finalize_claim_holder=? "
            "WHERE execution_id=? AND proof_emitted=0 "
            "AND (finalize_claim_until IS NULL OR finalize_claim_until < ?)",
            (lease_until, holder, execution_id, now),
        )
        conn.commit()
        return conn.total_changes == 1
    finally:
        conn.close()


def terminal_event_exists(execution_id: str) -> bool:
    """True when Event Service already holds proof or stalled for ``execution_id``."""
    for signal in ("cdp.generate.proof", "cdp.generate.stalled"):
        result = query_event_service(
            "signal-events",
            {"signal": signal, "execution_id": execution_id, "limit": 5},
        )
        for row in result.get("rows") or []:
            if not isinstance(row, dict):
                continue
            payload = row.get("payload")
            if isinstance(payload, str):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("execution_id") == execution_id
            ):
                return True
            if row.get("execution_id") == execution_id:
                return True
    return False


def clear_inflight_ledger() -> None:
    """Delete all in-flight leg rows (test isolation hook)."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM cdp_inflight_leg")
        conn.commit()
    finally:
        conn.close()
