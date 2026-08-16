"""Durable ledger for directive-loop mission negotiation state."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from universal_logging import get_logger

from services.git_integration_worker.cursor_auto.mission_negotiation_wire import (
    CanonicalMissionPayload,
    NegotiationState,
    parse_idle_deadline,
)
from services.git_integration_worker.cursor_dispatch_ledger import (
    _connect,
    _ledger_path,
)

logger = get_logger(__name__)

TERMINAL_STATES = frozenset({"RATIFIED", "EXPIRED", "ROUND_LIMIT", "REFUSED"})
_MAX_COUNTERS = 2

_DDL = """
CREATE TABLE IF NOT EXISTS mission_negotiations (
    thread_id           TEXT NOT NULL,
    negotiation_id      TEXT NOT NULL,
    state               TEXT NOT NULL,
    revision            INTEGER NOT NULL,
    proposal_hash       TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    counter_count       INTEGER NOT NULL DEFAULT 0,
    operator_agent      TEXT NOT NULL,
    idle_deadline       TEXT NOT NULL,
    latest_turn         INTEGER,
    last_duplicate_key  TEXT,
    PRIMARY KEY (thread_id, negotiation_id)
);
CREATE INDEX IF NOT EXISTS idx_mission_negotiations_thread
    ON mission_negotiations (thread_id);
CREATE TABLE IF NOT EXISTS mission_negotiation_duplicates (
    duplicate_key       TEXT PRIMARY KEY
);
"""


@dataclass(frozen=True, slots=True)
class NegotiationRow:
    """One durable negotiation ledger row."""

    thread_id: str
    negotiation_id: str
    state: NegotiationState
    revision: int
    proposal_hash: str
    payload: CanonicalMissionPayload
    counter_count: int
    operator_agent: str
    idle_deadline: str
    latest_turn: int | None


@dataclass(frozen=True, slots=True)
class TransitionResult:
    """Outcome of one ledger transition attempt."""

    ok: bool
    row: NegotiationRow | None = None
    duplicate: bool = False
    reason: str | None = None
    prior: NegotiationRow | None = None


def _payload_from_json(raw: str) -> CanonicalMissionPayload:
    data = json.loads(raw or "{}")
    return CanonicalMissionPayload(
        parent_thread=str(data.get("parent_thread") or ""),
        objective=str(data.get("objective") or ""),
        scope=str(data.get("scope") or ""),
        out_of_scope=str(data.get("out_of_scope") or ""),
        acceptance=str(data.get("acceptance") or ""),
        vision=str(data.get("vision") or ""),
    )


def _row_from_sql(row: sqlite3.Row) -> NegotiationRow:
    return NegotiationRow(
        thread_id=str(row["thread_id"]),
        negotiation_id=str(row["negotiation_id"]),
        state=row["state"],  # type: ignore[arg-type]
        revision=int(row["revision"]),
        proposal_hash=str(row["proposal_hash"]),
        payload=_payload_from_json(str(row["payload_json"])),
        counter_count=int(row["counter_count"]),
        operator_agent=str(row["operator_agent"]),
        idle_deadline=str(row["idle_deadline"]),
        latest_turn=(
            int(row["latest_turn"]) if row["latest_turn"] is not None else None
        ),
    )


def _duplicate_key(
    *,
    negotiation_id: str,
    revision: int,
    proposal_hash: str,
    in_reply_to_turn: int,
    sender: str,
) -> str:
    payload = {
        "negotiation_id": negotiation_id,
        "revision": revision,
        "proposal_hash": proposal_hash,
        "in_reply_to_turn": in_reply_to_turn,
        "sender": sender,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class MissionNegotiationLedger:
    """Restart-safe authority for mission negotiation control state."""

    _instance: MissionNegotiationLedger | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._db_path = _ledger_path()
        with self._connect() as conn:
            conn.executescript(_DDL)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    @classmethod
    def instance(cls) -> MissionNegotiationLedger:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        with cls._lock:
            cls._instance = None

    def get(self, thread_id: str, negotiation_id: str) -> NegotiationRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mission_negotiations WHERE thread_id=? AND negotiation_id=?",
                (thread_id, negotiation_id),
            ).fetchone()
        return _row_from_sql(row) if row is not None else None

    def open_on_thread(self, thread_id: str) -> NegotiationRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM mission_negotiations WHERE thread_id=? "
                "AND state NOT IN ('RATIFIED','EXPIRED','ROUND_LIMIT','REFUSED') "
                "ORDER BY revision DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        return _row_from_sql(row) if row is not None else None

    def is_duplicate(
        self,
        *,
        negotiation_id: str,
        revision: int,
        proposal_hash: str,
        in_reply_to_turn: int,
        sender: str,
    ) -> bool:
        key = _duplicate_key(
            negotiation_id=negotiation_id,
            revision=revision,
            proposal_hash=proposal_hash,
            in_reply_to_turn=in_reply_to_turn,
            sender=sender,
        )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM mission_negotiation_duplicates WHERE duplicate_key=?",
                (key,),
            ).fetchone()
        return row is not None

    def expire_idle(self, thread_id: str, negotiation_id: str) -> TransitionResult:
        row = self.get(thread_id, negotiation_id)
        if row is None or row.state in TERMINAL_STATES:
            return TransitionResult(ok=False, reason="negotiation.state_refused", prior=row)
        deadline = parse_idle_deadline(row.idle_deadline)
        if deadline is None or datetime.now(UTC) <= deadline:
            return TransitionResult(ok=False, reason="negotiation.not_expired", prior=row)
        self._set_state(thread_id, negotiation_id, "EXPIRED")
        updated = self.get(thread_id, negotiation_id)
        return TransitionResult(ok=True, row=updated, prior=row)

    def apply_transition(
        self,
        *,
        thread_id: str,
        negotiation_id: str,
        phase: str,
        revision: int,
        proposal_hash: str,
        payload: CanonicalMissionPayload,
        in_reply_to_turn: int,
        sender: str,
        operator_agent: str,
        idle_deadline: str,
        request_turn: int,
    ) -> TransitionResult:
        dup_key = _duplicate_key(
            negotiation_id=negotiation_id,
            revision=revision,
            proposal_hash=proposal_hash,
            in_reply_to_turn=in_reply_to_turn,
            sender=sender,
        )
        existing = self.get(thread_id, negotiation_id)
        if self.is_duplicate(
            negotiation_id=negotiation_id,
            revision=revision,
            proposal_hash=proposal_hash,
            in_reply_to_turn=in_reply_to_turn,
            sender=sender,
        ):
            return TransitionResult(
                ok=True,
                duplicate=True,
                row=existing,
                prior=existing,
            )
        if existing is not None:
            expired = self._maybe_expire(existing)
            if expired is not None:
                existing = expired
        if existing is not None and existing.state in TERMINAL_STATES:
            return TransitionResult(
                ok=False,
                reason="negotiation.state_refused",
                prior=existing,
            )
        if phase == "proposal":
            if existing is not None:
                return TransitionResult(
                    ok=False,
                    reason="negotiation.state_refused",
                    prior=existing,
                )
            row = NegotiationRow(
                thread_id=thread_id,
                negotiation_id=negotiation_id,
                state="OPEN",
                revision=1,
                proposal_hash=proposal_hash,
                payload=payload,
                counter_count=0,
                operator_agent=operator_agent,
                idle_deadline=idle_deadline,
                latest_turn=request_turn,
            )
            self._insert(row=row, duplicate_key=dup_key)
            return TransitionResult(ok=True, row=row)

        if existing is None:
            return TransitionResult(ok=False, reason="negotiation.stale_refused")

        if revision != existing.revision + 1:
            return TransitionResult(
                ok=False,
                reason="negotiation.stale_refused",
                prior=existing,
            )
        if phase == "counter":
            if existing.counter_count >= _MAX_COUNTERS:
                self._set_state(thread_id, negotiation_id, "ROUND_LIMIT")
                updated = self.get(thread_id, negotiation_id)
                return TransitionResult(
                    ok=False,
                    reason="negotiation.round_limit",
                    row=updated,
                    prior=existing,
                )
            row = NegotiationRow(
                thread_id=thread_id,
                negotiation_id=negotiation_id,
                state="OPEN",
                revision=revision,
                proposal_hash=proposal_hash,
                payload=payload,
                counter_count=existing.counter_count + 1,
                operator_agent=operator_agent,
                idle_deadline=idle_deadline,
                latest_turn=request_turn,
            )
            self._update(row=row, duplicate_key=dup_key)
            return TransitionResult(ok=True, row=row, prior=existing)

        if phase == "agree":
            if existing.proposal_hash != proposal_hash:
                return TransitionResult(
                    ok=False,
                    reason="negotiation.hash_refused",
                    prior=existing,
                )
            if existing.payload.as_dict() != payload.as_dict():
                return TransitionResult(
                    ok=False,
                    reason="negotiation.scope_refused",
                    prior=existing,
                )
            row = NegotiationRow(
                thread_id=thread_id,
                negotiation_id=negotiation_id,
                state="AWAITING_RATIFICATION",
                revision=revision,
                proposal_hash=proposal_hash,
                payload=payload,
                counter_count=existing.counter_count,
                operator_agent=operator_agent,
                idle_deadline=idle_deadline,
                latest_turn=request_turn,
            )
            self._update(row=row, duplicate_key=dup_key)
            return TransitionResult(ok=True, row=row, prior=existing)

        if phase == "ratify":
            if existing.state != "AWAITING_RATIFICATION":
                return TransitionResult(
                    ok=False,
                    reason="negotiation.state_refused",
                    prior=existing,
                )
            if existing.proposal_hash != proposal_hash:
                return TransitionResult(
                    ok=False,
                    reason="negotiation.hash_refused",
                    prior=existing,
                )
            if in_reply_to_turn != (existing.latest_turn or 0):
                return TransitionResult(
                    ok=False,
                    reason="negotiation.stale_refused",
                    prior=existing,
                )
            row = NegotiationRow(
                thread_id=thread_id,
                negotiation_id=negotiation_id,
                state="RATIFIED",
                revision=revision,
                proposal_hash=proposal_hash,
                payload=payload,
                counter_count=existing.counter_count,
                operator_agent=operator_agent,
                idle_deadline=idle_deadline,
                latest_turn=request_turn,
            )
            self._update(row=row, duplicate_key=dup_key)
            return TransitionResult(ok=True, row=row, prior=existing)

        return TransitionResult(ok=False, reason="negotiation.malformed", prior=existing)

    def _maybe_expire(self, row: NegotiationRow) -> NegotiationRow | None:
        deadline = parse_idle_deadline(row.idle_deadline)
        if deadline is None or datetime.now(UTC) <= deadline:
            return None
        self._set_state(row.thread_id, row.negotiation_id, "EXPIRED")
        return self.get(row.thread_id, row.negotiation_id)

    def _insert(self, *, row: NegotiationRow, duplicate_key: str) -> None:
        payload_json = json.dumps(row.payload.as_dict(), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mission_negotiations "
                "(thread_id, negotiation_id, state, revision, proposal_hash, "
                "payload_json, counter_count, operator_agent, idle_deadline, "
                "latest_turn, last_duplicate_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.thread_id,
                    row.negotiation_id,
                    row.state,
                    row.revision,
                    row.proposal_hash,
                    payload_json,
                    row.counter_count,
                    row.operator_agent,
                    row.idle_deadline,
                    row.latest_turn,
                    duplicate_key,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO mission_negotiation_duplicates (duplicate_key) "
                "VALUES (?)",
                (duplicate_key,),
            )

    def _update(self, *, row: NegotiationRow, duplicate_key: str) -> None:
        payload_json = json.dumps(row.payload.as_dict(), sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                "UPDATE mission_negotiations SET state=?, revision=?, proposal_hash=?, "
                "payload_json=?, counter_count=?, operator_agent=?, idle_deadline=?, "
                "latest_turn=?, last_duplicate_key=? "
                "WHERE thread_id=? AND negotiation_id=?",
                (
                    row.state,
                    row.revision,
                    row.proposal_hash,
                    payload_json,
                    row.counter_count,
                    row.operator_agent,
                    row.idle_deadline,
                    row.latest_turn,
                    duplicate_key,
                    row.thread_id,
                    row.negotiation_id,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO mission_negotiation_duplicates (duplicate_key) "
                "VALUES (?)",
                (duplicate_key,),
            )

    def _set_state(
        self, thread_id: str, negotiation_id: str, state: NegotiationState
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE mission_negotiations SET state=? "
                "WHERE thread_id=? AND negotiation_id=?",
                (state, thread_id, negotiation_id),
            )


def get_negotiation_ledger() -> MissionNegotiationLedger:
    """Return the process-global mission negotiation ledger."""
    return MissionNegotiationLedger.instance()
