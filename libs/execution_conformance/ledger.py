"""SQLite local intent ledger — durable before any remote actuator call."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .manifest import ActuatorName
from .states import ExecutionState, IllegalStateTransitionError, assert_transition


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
    store_epoch     TEXT NOT NULL DEFAULT '',
    payload_json    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(actuator, identity_key)
);
CREATE TABLE IF NOT EXISTS execution_compensations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id       TEXT NOT NULL,
    authorized_by   TEXT NOT NULL,
    reason          TEXT NOT NULL,
    prior_state     TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class IntentRecord:
    intent_id: str
    actuator: ActuatorName
    identity_key: str
    state: ExecutionState
    actuator_ref: str | None
    retry_count: int
    store_epoch: str
    payload: dict[str, Any] | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ReserveOutcome:
    record: IntentRecord
    created: bool


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
        conn.execute("PRAGMA synchronous=FULL")
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(_DDL)
            conn.commit()

    def reserve(
        self,
        *,
        intent_id: str,
        actuator: ActuatorName,
        identity_key: str,
        store_epoch: str,
        payload: dict[str, Any] | None = None,
    ) -> ReserveOutcome:
        existing = self.find_by_identity(actuator=actuator, identity_key=identity_key)
        if existing is not None:
            return ReserveOutcome(record=existing, created=False)
        ts = _utc_now()
        payload_json = json.dumps(payload) if payload is not None else None
        with closing(self._connect()) as conn:
            try:
                conn.execute(
                    "INSERT INTO execution_intents "
                    "(intent_id, actuator, identity_key, state, actuator_ref, retry_count, "
                    "store_epoch, payload_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, NULL, 0, ?, ?, ?, ?)",
                    (
                        intent_id,
                        actuator,
                        identity_key,
                        ExecutionState.reserved.value,
                        store_epoch,
                        payload_json,
                        ts,
                        ts,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                row = self.find_by_identity(
                    actuator=actuator, identity_key=identity_key
                )
                if row is None:
                    raise
                return ReserveOutcome(record=row, created=False)
        row = self.get(intent_id)
        if row is None:
            raise RuntimeError(f"reserve failed to read back {intent_id!r}")
        return ReserveOutcome(record=row, created=True)

    def get(self, intent_id: str) -> IntentRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def find_by_identity(
        self, *, actuator: ActuatorName, identity_key: str
    ) -> IntentRecord | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM execution_intents "
                "WHERE actuator = ? AND identity_key = ? LIMIT 1",
                (actuator, identity_key),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def set_state_cas(
        self, intent_id: str, expected: ExecutionState, target: ExecutionState
    ) -> IntentRecord | None:
        if expected == target:
            row = self.get(intent_id)
            return row
        assert_transition(expected, target)
        ts = _utc_now()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE execution_intents SET state = ?, updated_at = ? "
                "WHERE intent_id = ? AND state = ?",
                (target.value, ts, intent_id, expected.value),
            )
            conn.commit()
            if cur.rowcount != 1:
                return None
        return self.get(intent_id)

    def set_state(self, intent_id: str, state: ExecutionState) -> IntentRecord:
        current = self.get(intent_id)
        if current is None:
            raise KeyError(f"unknown intent_id {intent_id!r}")
        if current.state == state:
            return current
        row = self.set_state_cas(intent_id, current.state, state)
        if row is None:
            fresh = self.get(intent_id)
            if fresh is None:
                raise KeyError(intent_id)
            raise IllegalStateTransitionError(
                f"cas set_state lost race on {intent_id!r}"
            )
        return row

    def record_receipt_cas(
        self,
        intent_id: str,
        *,
        expected: ExecutionState,
        actuator_ref: str,
        state: ExecutionState,
    ) -> IntentRecord | None:
        assert_transition(expected, state)
        ts = _utc_now()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE execution_intents "
                "SET state = ?, actuator_ref = ?, updated_at = ? "
                "WHERE intent_id = ? AND state = ?",
                (state.value, actuator_ref, ts, intent_id, expected.value),
            )
            conn.commit()
            if cur.rowcount != 1:
                return None
        return self.get(intent_id)

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
        row = self.record_receipt_cas(
            intent_id,
            expected=current.state,
            actuator_ref=actuator_ref,
            state=state,
        )
        if row is None:
            raise IllegalStateTransitionError(
                f"cas record_receipt lost race on {intent_id!r}"
            )
        return row

    def patch_actuator_ref_if_null(
        self, intent_id: str, actuator_ref: str
    ) -> IntentRecord | None:
        ts = _utc_now()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "UPDATE execution_intents SET actuator_ref = ?, updated_at = ? "
                "WHERE intent_id = ? AND actuator_ref IS NULL",
                (actuator_ref, ts, intent_id),
            )
            conn.commit()
            if cur.rowcount != 1:
                return None
        return self.get(intent_id)

    def claim_retry(self, intent_id: str) -> bool:
        """Atomically increment retry_count once for eligible states."""
        ts = _utc_now()
        allowed = (
            ExecutionState.reserved.value,
            ExecutionState.outcome_unknown.value,
        )
        placeholders = ",".join("?" for _ in allowed)
        with closing(self._connect()) as conn:
            cur = conn.execute(
                f"UPDATE execution_intents SET retry_count = retry_count + 1, "
                f"updated_at = ? WHERE intent_id = ? AND retry_count = 0 "
                f"AND state IN ({placeholders})",
                (ts, intent_id, *allowed),
            )
            conn.commit()
            return cur.rowcount == 1

    def merge_payload(self, intent_id: str, patch: dict[str, Any]) -> IntentRecord:
        row = self.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        merged = dict(row.payload or {})
        merged.update(patch)
        ts = _utc_now()
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE execution_intents SET payload_json = ?, updated_at = ? "
                "WHERE intent_id = ?",
                (json.dumps(merged), ts, intent_id),
            )
            conn.commit()
        updated = self.get(intent_id)
        if updated is None:
            raise KeyError(intent_id)
        return updated

    def floor_retry(self, intent_id: str) -> None:
        ts = _utc_now()
        with closing(self._connect()) as conn:
            conn.execute(
                "UPDATE execution_intents SET retry_count = MAX(retry_count, 1), "
                "updated_at = ? WHERE intent_id = ?",
                (ts, intent_id),
            )
            conn.commit()

    def apply_compensation(
        self,
        intent_id: str,
        *,
        authorized_by: str,
        reason: str,
        prior_state: ExecutionState,
    ) -> IntentRecord:
        ts = _utc_now()
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO execution_compensations "
                "(intent_id, authorized_by, reason, prior_state, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (intent_id, authorized_by, reason, prior_state.value, ts),
            )
            conn.execute(
                "UPDATE execution_intents SET state = ?, retry_count = MAX(retry_count, 1), "
                "updated_at = ? WHERE intent_id = ? AND state = ?",
                (
                    ExecutionState.outcome_unknown.value,
                    ts,
                    intent_id,
                    prior_state.value,
                ),
            )
            if conn.total_changes == 0:
                raise IllegalStateTransitionError(
                    f"compensation cas failed for {intent_id!r}"
                )
            conn.commit()
        row = self.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return row

    def reopen(self) -> IntentLedger:
        return IntentLedger(self._db_path)


def _row_to_record(row: sqlite3.Row) -> IntentRecord:
    payload_raw = row["payload_json"]
    payload = json.loads(payload_raw) if payload_raw else None
    keys = set(row.keys())
    store_epoch = row["store_epoch"] if "store_epoch" in keys else ""
    return IntentRecord(
        intent_id=row["intent_id"],
        actuator=row["actuator"],  # type: ignore[arg-type]
        identity_key=row["identity_key"],
        state=ExecutionState(row["state"]),
        actuator_ref=row["actuator_ref"],
        retry_count=int(row["retry_count"]),
        store_epoch=str(store_epoch or ""),
        payload=payload,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
