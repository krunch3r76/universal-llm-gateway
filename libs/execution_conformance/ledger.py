"""SQLite local intent ledger — durable before any remote actuator call."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .states import ExecutionState, assert_transition


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


_DDL = """
CREATE TABLE IF NOT EXISTS execution_intents (
    intent_id       TEXT PRIMARY KEY,
    actuator        TEXT NOT NULL,
    identity_key    TEXT NOT NULL,
    state           TEXT NOT NULL,
    actuator_ref    TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    payload_json    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execution_intents_identity
    ON execution_intents(actuator, identity_key);
"""


@dataclass(frozen=True, slots=True)
class IntentRecord:
    intent_id: str
    actuator: str
    identity_key: str
    state: ExecutionState
    actuator_ref: str | None
    retry_count: int
    payload: dict[str, Any] | None
    created_at: str
    updated_at: str


class IntentLedger:
    """Caller-minted ``intent_id`` rows persisted at ``reserved`` before remote I/O."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)
            conn.commit()

    def reserve(
        self,
        *,
        intent_id: str,
        actuator: str,
        identity_key: str,
        payload: dict[str, Any] | None = None,
    ) -> IntentRecord:
        ts = _utc_now()
        payload_json = json.dumps(payload) if payload is not None else None
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO execution_intents "
                "(intent_id, actuator, identity_key, state, actuator_ref, retry_count, "
                "payload_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, 0, ?, ?, ?)",
                (
                    intent_id,
                    actuator,
                    identity_key,
                    ExecutionState.reserved.value,
                    payload_json,
                    ts,
                    ts,
                ),
            )
            conn.commit()
        row = self.get(intent_id)
        assert row is not None
        return row

    def get(self, intent_id: str) -> IntentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def find_by_identity(
        self, *, actuator: str, identity_key: str
    ) -> IntentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM execution_intents "
                "WHERE actuator = ? AND identity_key = ? "
                "ORDER BY created_at ASC LIMIT 1",
                (actuator, identity_key),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def record_receipt(
        self,
        intent_id: str,
        *,
        actuator_ref: str,
        state: ExecutionState,
    ) -> IntentRecord:
        current = self.get(intent_id)
        if current is None:
            raise KeyError(f"unknown intent_id {intent_id!r}")
        assert_transition(current.state, state)
        ts = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE execution_intents "
                "SET state = ?, actuator_ref = ?, updated_at = ? "
                "WHERE intent_id = ?",
                (state.value, actuator_ref, ts, intent_id),
            )
            conn.commit()
        row = self.get(intent_id)
        assert row is not None
        return row

    def set_state(self, intent_id: str, state: ExecutionState) -> IntentRecord:
        current = self.get(intent_id)
        if current is None:
            raise KeyError(f"unknown intent_id {intent_id!r}")
        assert_transition(current.state, state)
        ts = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE execution_intents SET state = ?, updated_at = ? WHERE intent_id = ?",
                (state.value, ts, intent_id),
            )
            conn.commit()
        row = self.get(intent_id)
        assert row is not None
        return row

    def patch_actuator_ref(self, intent_id: str, actuator_ref: str) -> IntentRecord:
        ts = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE execution_intents SET actuator_ref = ?, updated_at = ? "
                "WHERE intent_id = ?",
                (actuator_ref, ts, intent_id),
            )
            conn.commit()
        row = self.get(intent_id)
        assert row is not None
        return row

    def increment_retry(self, intent_id: str) -> IntentRecord:
        ts = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE execution_intents SET retry_count = retry_count + 1, updated_at = ? "
                "WHERE intent_id = ?",
                (ts, intent_id),
            )
            conn.commit()
        row = self.get(intent_id)
        assert row is not None
        return row

    def reopen(self) -> IntentLedger:
        """Return a fresh handle over the same on-disk ledger (process restart model)."""
        return IntentLedger(self._db_path)


def _row_to_record(row: sqlite3.Row) -> IntentRecord:
    payload_raw = row["payload_json"]
    payload = json.loads(payload_raw) if payload_raw else None
    return IntentRecord(
        intent_id=row["intent_id"],
        actuator=row["actuator"],
        identity_key=row["identity_key"],
        state=ExecutionState(row["state"]),
        actuator_ref=row["actuator_ref"],
        retry_count=int(row["retry_count"]),
        payload=payload,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
