"""Reference SQLite intent ledger — conformance fixture only (see module docstring)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .manifest import ActuatorName
from .states import (
    TERMINAL_STATES,
    ExecutionState,
    IllegalStateTransitionError,
    assert_transition,
)


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


def _payload_inflight(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    raw = payload.get("inflight_claimed_at")
    if raw is None:
        return None
    return float(raw)


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
    inflight_claimed_at: float | None = None


@dataclass(frozen=True, slots=True)
class ReserveOutcome:
    record: IntentRecord
    created: bool


@runtime_checkable
class IntentStore(Protocol):
    """Durable local intent persistence (production: ``libs/cortex_store`` aggregate).

    Methods mirror the reference SQLite fixture; CAS helpers enforce single-row updates.
    ``blocked_unknown`` exits only via ``apply_compensation``, ``promote_blocked_adoption``,
    or ``apply_blocked_resolution`` — not public ``set_state``.
    """

    def reserve(
        self,
        *,
        intent_id: str,
        actuator: ActuatorName,
        identity_key: str,
        store_epoch: str,
        payload: dict[str, Any] | None = None,
    ) -> ReserveOutcome: ...

    def get(self, intent_id: str) -> IntentRecord | None: ...

    def find_by_identity(
        self, *, actuator: ActuatorName, identity_key: str
    ) -> IntentRecord | None: ...

    def set_state(self, intent_id: str, state: ExecutionState) -> IntentRecord: ...

    def record_receipt(
        self,
        intent_id: str,
        *,
        actuator_ref: str,
        state: ExecutionState,
    ) -> IntentRecord: ...

    def patch_actuator_ref_if_null(
        self, intent_id: str, actuator_ref: str
    ) -> IntentRecord | None: ...

    def claim_retry(self, intent_id: str, *, inflight_at: float | None = None) -> bool: ...

    def stamp_inflight(self, intent_id: str, claimed_at: float) -> IntentRecord: ...

    def clear_inflight_marker(self, intent_id: str) -> None: ...

    def merge_payload(self, intent_id: str, patch: dict[str, Any]) -> IntentRecord: ...

    def floor_retry(self, intent_id: str) -> None: ...

    def apply_compensation(
        self,
        intent_id: str,
        *,
        authorized_by: str,
        reason: str,
        prior_state: ExecutionState,
    ) -> IntentRecord: ...

    def promote_blocked_adoption(
        self,
        intent_id: str,
        *,
        target: ExecutionState,
        actuator_ref: str | None,
        owner_unknown_reason: str | None = None,
    ) -> IntentRecord: ...

    def apply_blocked_resolution(
        self,
        intent_id: str,
        *,
        authorized_by: str,
        reason: str,
        target_state: ExecutionState,
        prior_state: ExecutionState,
    ) -> IntentRecord: ...


class ReferenceSqliteIntentLedger:
    """Reference / conformance-fixture ``IntentStore`` — NOT production operational authority."""

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
        conn.execute("PRAGMA busy_timeout=5000")
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

    def stamp_inflight(self, intent_id: str, claimed_at: float) -> IntentRecord:
        return self.merge_payload(intent_id, {"inflight_claimed_at": claimed_at})

    def clear_inflight_marker(self, intent_id: str) -> None:
        row = self.get(intent_id)
        if row is None or not row.payload or "inflight_claimed_at" not in row.payload:
            return
        payload = dict(row.payload)
        payload.pop("inflight_claimed_at", None)
        self.merge_payload(intent_id, payload)

    def set_state_cas(
        self, intent_id: str, expected: ExecutionState, target: ExecutionState
    ) -> IntentRecord | None:
        if expected == target:
            return self.get(intent_id)
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
        if current.state == ExecutionState.blocked_unknown:
            if state in TERMINAL_STATES:
                raise IllegalStateTransitionError(
                    f"blocked_unknown → {state.value} requires resolution helper"
                )
            if state == ExecutionState.outcome_unknown:
                raise IllegalStateTransitionError(
                    "blocked_unknown → outcome_unknown requires apply_compensation "
                    "or promote_blocked_adoption"
                )
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
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload_json FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            payload = dict(
                json.loads(row["payload_json"]) if row["payload_json"] else {}
            )
            payload.pop("inflight_claimed_at", None)
            cur = conn.execute(
                "UPDATE execution_intents "
                "SET state = ?, actuator_ref = ?, payload_json = ?, updated_at = ? "
                "WHERE intent_id = ? AND state = ?",
                (
                    state.value,
                    actuator_ref,
                    json.dumps(payload),
                    ts,
                    intent_id,
                    expected.value,
                ),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return None
            conn.commit()
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

    def claim_retry(self, intent_id: str, *, inflight_at: float | None = None) -> bool:
        ts = _utc_now()
        allowed = (
            ExecutionState.reserved.value,
            ExecutionState.outcome_unknown.value,
        )
        placeholders = ",".join("?" for _ in allowed)
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload_json, retry_count, state FROM execution_intents "
                "WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return False
            if row["retry_count"] != 0 or row["state"] not in allowed:
                conn.execute("ROLLBACK")
                return False
            payload = dict(
                json.loads(row["payload_json"]) if row["payload_json"] else {}
            )
            if inflight_at is not None:
                payload["inflight_claimed_at"] = inflight_at
            cur = conn.execute(
                f"UPDATE execution_intents SET retry_count = retry_count + 1, "
                f"payload_json = ?, updated_at = ? WHERE intent_id = ? AND retry_count = 0 "
                f"AND state IN ({placeholders})",
                (json.dumps(payload), ts, intent_id, *allowed),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return False
            conn.commit()
            return True

    def merge_payload(self, intent_id: str, patch: dict[str, Any]) -> IntentRecord:
        ts = _utc_now()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT payload_json FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise KeyError(intent_id)
            merged = dict(
                json.loads(row["payload_json"]) if row["payload_json"] else {}
            )
            merged.update(patch)
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

    def promote_blocked_adoption(
        self,
        intent_id: str,
        *,
        target: ExecutionState,
        actuator_ref: str | None,
        owner_unknown_reason: str | None = None,
    ) -> IntentRecord:
        ts = _utc_now()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state, payload_json, actuator_ref FROM execution_intents "
                "WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None or row["state"] != ExecutionState.blocked_unknown.value:
                conn.execute("ROLLBACK")
                raise IllegalStateTransitionError(
                    f"promote_blocked_adoption requires blocked_unknown on {intent_id!r}"
                )
            payload = dict(
                json.loads(row["payload_json"]) if row["payload_json"] else {}
            )
            payload.pop("blocked_reason", None)
            payload.pop("inflight_claimed_at", None)
            if owner_unknown_reason is not None:
                payload["owner_unknown_reason"] = owner_unknown_reason
            ref = actuator_ref or row["actuator_ref"]
            cur = conn.execute(
                "UPDATE execution_intents SET state = ?, actuator_ref = ?, "
                "payload_json = ?, updated_at = ? WHERE intent_id = ? AND state = ?",
                (
                    target.value,
                    ref,
                    json.dumps(payload),
                    ts,
                    intent_id,
                    ExecutionState.blocked_unknown.value,
                ),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                raise IllegalStateTransitionError(
                    f"promote_blocked_adoption cas failed for {intent_id!r}"
                )
            conn.commit()
        updated = self.get(intent_id)
        if updated is None:
            raise KeyError(intent_id)
        return updated

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
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO execution_compensations "
                "(intent_id, authorized_by, reason, prior_state, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (intent_id, authorized_by, reason, prior_state.value, ts),
            )
            row = conn.execute(
                "SELECT payload_json FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            payload = dict(
                json.loads(row["payload_json"]) if row and row["payload_json"] else {}
            )
            payload.pop("inflight_claimed_at", None)
            cur = conn.execute(
                "UPDATE execution_intents SET state = ?, retry_count = MAX(retry_count, 1), "
                "payload_json = ?, updated_at = ? WHERE intent_id = ? AND state = ?",
                (
                    ExecutionState.outcome_unknown.value,
                    json.dumps(payload),
                    ts,
                    intent_id,
                    prior_state.value,
                ),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                raise IllegalStateTransitionError(
                    f"compensation cas failed for {intent_id!r}"
                )
            conn.commit()
        row = self.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return row

    def apply_blocked_resolution(
        self,
        intent_id: str,
        *,
        authorized_by: str,
        reason: str,
        target_state: ExecutionState,
        prior_state: ExecutionState,
    ) -> IntentRecord:
        if target_state not in (ExecutionState.failed, ExecutionState.cancelled):
            raise ValueError("blocked resolution target must be failed or cancelled")
        ts = _utc_now()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO execution_compensations "
                "(intent_id, authorized_by, reason, prior_state, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (intent_id, authorized_by, reason, prior_state.value, ts),
            )
            row = conn.execute(
                "SELECT payload_json FROM execution_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            payload = dict(
                json.loads(row["payload_json"]) if row and row["payload_json"] else {}
            )
            payload.pop("inflight_claimed_at", None)
            cur = conn.execute(
                "UPDATE execution_intents SET state = ?, retry_count = MAX(retry_count, 1), "
                "payload_json = ?, updated_at = ? WHERE intent_id = ? AND state = ?",
                (
                    target_state.value,
                    json.dumps(payload),
                    ts,
                    intent_id,
                    prior_state.value,
                ),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                raise IllegalStateTransitionError(
                    f"blocked resolution cas failed for {intent_id!r}"
                )
            conn.commit()
        row = self.get(intent_id)
        if row is None:
            raise KeyError(intent_id)
        return row

    def apply_ref_conflict_resolution(
        self,
        intent_id: str,
        *,
        authorized_by: str,
        reason: str,
        target_state: ExecutionState,
        prior_state: ExecutionState,
    ) -> IntentRecord:
        return self.apply_blocked_resolution(
            intent_id,
            authorized_by=authorized_by,
            reason=reason,
            target_state=target_state,
            prior_state=prior_state,
        )

    def reopen(self) -> ReferenceSqliteIntentLedger:
        return ReferenceSqliteIntentLedger(self._db_path)


def _row_to_record(row: sqlite3.Row) -> IntentRecord:
    payload_raw = row["payload_json"]
    payload = json.loads(payload_raw) if payload_raw else None
    keys = set(row.keys())
    store_epoch = row["store_epoch"] if "store_epoch" in keys else ""
    inflight = _payload_inflight(payload)
    return IntentRecord(
        intent_id=row["intent_id"],
        actuator=row["actuator"],
        identity_key=row["identity_key"],
        state=ExecutionState(row["state"]),
        actuator_ref=row["actuator_ref"],
        retry_count=int(row["retry_count"]),
        store_epoch=str(store_epoch or ""),
        payload=payload,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        inflight_claimed_at=inflight,
    )
