"""Durable restart-intent state for manage-initiated deferred restarts (R-D).

SQLite-backed (mirrors the cursor-dispatch ledger pattern: a small WAL db under
the manage data dir, ``GATEWAY_DIR`` = ``~/.gateway``). One row per restart
intent. ``_BLOCKS_NEW_RESTART`` statuses coalesce concurrent restarts; a partial
unique index backstops that predicate. ``_NEEDS_RECONCILE`` is the boot feed and
also includes ``verifying_activation`` so activation proof survives manage
restart without a second begin-drain.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from universal_logging import get_logger

from .restart_intent_migrate import _DDL, apply_restart_intent_schema
from .restart_intent_reason_codes import reason_code as _reason_code
from .restart_intent_states import (
    _ALL_STATUSES,
    _BLOCKS_NEW_RESTART,
    _NEEDS_RECONCILE,
    _TERMINAL,
    LIVE_STATUSES,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_FAILED,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
    STATUS_TIMEOUT,
    STATUS_VERIFYING_ACTIVATION,
)
from .restart_intent_transitions import (
    append_kill_commit,
    append_status_transition,
    transition_fields,
)
from .restart_window_store import (
    RestartWindow,
    RestartWindowStore,
    RestartWindowView,
)
from .service_config import GATEWAY_DIR

logger = get_logger(__name__)

if TYPE_CHECKING:
    from .owed_wake_ledger import OwedWakeRow


class ArmResultKind(StrEnum):
    """Outcome of ``arm_with_boundary`` (S2)."""

    ARMED = "armed"
    NOT_APPLICABLE = "not_applicable"
    LOST_RACE = "lost_race"


@dataclass(frozen=True, slots=True)
class ArmResult:
    kind: ArmResultKind


class RestartIntentCancelError(Exception):
    """Cancel refused: unknown id, already terminal (non-cancelled), or past kill commit."""

    def __init__(self, reason: str, *, status: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _default_path() -> Path:
    GATEWAY_DIR.mkdir(parents=True, exist_ok=True)
    return GATEWAY_DIR / "restart-intents.db"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@dataclass(slots=True)
class Intent:
    """One restart intent (mirrors the ``restart_intents`` row)."""

    intent_id: str
    service: str
    action: str
    status: str
    drain_epoch: int | None
    worker_id: str | None
    worker_started_at: str | None
    deadline_at: str | None
    last_seen_event_seq: int
    reason: str | None
    kill_boundary_at: str | None
    created_at: str
    updated_at: str
    park_live: bool = False
    park_summary: dict[str, Any] | None = None
    wait_for_boundary: bool = False
    caller_agent: str | None = None
    caller_thread_id: str | None = None
    arming_lanes: list[str] | None = None
    armed_at: str | None = None
    expires_at: str | None = None
    status_reason: str | None = None
    status_changed_at: str | None = None
    transitions: list[dict[str, str]] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


class IntentStatusView(TypedDict):
    """Operator-facing restart-intent snapshot for manage status queries."""

    restart_intent_id: str
    status: str
    drain_epoch: int | None
    deadline_at: str | None
    elapsed_s: int


def intent_status_view(intent: Intent, *, now: datetime) -> IntentStatusView:
    """Project a durable restart intent into a stable status dict for callers."""
    created = datetime.fromisoformat(intent.created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "restart_intent_id": intent.intent_id,
        "status": intent.status,
        "drain_epoch": intent.drain_epoch,
        "deadline_at": intent.deadline_at,
        "elapsed_s": round((now - created).total_seconds()),
    }


def _row_to_intent(row: sqlite3.Row) -> Intent:
    keys = row.keys()
    return Intent(
        intent_id=row["intent_id"],
        service=row["service"],
        action=row["action"],
        status=row["status"],
        drain_epoch=row["drain_epoch"],
        worker_id=row["worker_id"],
        worker_started_at=row["worker_started_at"],
        deadline_at=row["deadline_at"],
        last_seen_event_seq=row["last_seen_event_seq"],
        reason=row["reason"],
        kill_boundary_at=row["kill_boundary_at"]
        if "kill_boundary_at" in keys
        else None,
        park_live=bool(row["park_live"]) if "park_live" in keys else False,
        park_summary=_decode_park_summary(row["park_summary"])
        if "park_summary" in keys
        else None,
        wait_for_boundary=bool(row["wait_for_boundary"])
        if "wait_for_boundary" in keys
        else False,
        caller_agent=row["caller_agent"] if "caller_agent" in keys else None,
        caller_thread_id=row["caller_thread_id"] if "caller_thread_id" in keys else None,
        arming_lanes=_decode_arming_lanes(row["arming_lanes"])
        if "arming_lanes" in keys
        else None,
        armed_at=row["armed_at"] if "armed_at" in keys else None,
        expires_at=row["expires_at"] if "expires_at" in keys else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        status_reason=transition_fields(row)[0],
        status_changed_at=transition_fields(row)[1],
        transitions=transition_fields(row)[2],
    )


def _decode_arming_lanes(raw: Any) -> list[str] | None:
    from .owed_wake_ledger import decode_arming_lanes

    lanes = decode_arming_lanes(raw)
    return lanes or None


def _encode_arming_lanes(lanes: list[str] | None) -> str | None:
    if not lanes:
        return None
    cleaned = [str(x).strip() for x in lanes if str(x).strip()]
    if not cleaned:
        return None
    return json.dumps(cleaned, separators=(",", ":"))


def _decode_park_summary(raw: Any) -> dict[str, Any] | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        parsed = json.loads(str(raw))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _encode_park_summary(summary: dict[str, Any]) -> str:
    return json.dumps(summary, separators=(",", ":"), sort_keys=True)


class RestartIntentStore:
    """Durable singleton for restart intents. DB methods are synchronous."""

    _instance: RestartIntentStore | None = None

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path: Path = db_path or _default_path()
        with self._connect() as conn:
            conn.executescript(_DDL)
            apply_restart_intent_schema(conn)
            conn.commit()
        self._windows = RestartWindowStore(self._db_path, self._connect)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    @classmethod
    def instance(cls) -> RestartIntentStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def create_intent(
        self,
        *,
        service: str,
        action: str,
        deadline_at: str,
        reason: str,
        park_live: bool = False,
        wait_for_boundary: bool = False,
        intent_ttl_s: float | None = None,
        caller_agent: str | None = None,
        caller_thread_id: str | None = None,
        joiner_thread_id: str | None = None,
    ) -> Intent:
        """INSERT ``pending_drain``, or refresh the existing row's window.

        A joining request does not replace ``caller_agent`` or clear
        ``wait_for_boundary``. Expiry is not applied here — that is the cancel
        path, not insert. ``wait_for_boundary`` defaults to no hard expiry
        unless ``intent_ttl_s`` is set (a:37197).
        """
        from .restart_intent_expiry import (
            arm_stamps,
            join_expires_at,
            resolve_intent_ttl_s,
        )

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM restart_intents WHERE service=? AND status IN "
                f"({','.join('?' * len(_BLOCKS_NEW_RESTART))}) ORDER BY created_at LIMIT 1",
                (service, *_BLOCKS_NEW_RESTART),
            ).fetchone()
            if existing is not None:
                prior = _row_to_intent(existing)
                wfb = prior.wait_for_boundary or wait_for_boundary
                clock = datetime.now(UTC)
                _agent, armed, _ignored = arm_stamps(
                    prior.caller_agent, now=clock, ttl_s=None
                )
                expires = join_expires_at(
                    wait_for_boundary=wfb,
                    intent_ttl_s=intent_ttl_s,
                    prior_expires_at=prior.expires_at,
                    now=clock,
                )
                merged_lanes = _merge_arming_lanes(
                    _decode_arming_lanes(existing["arming_lanes"])
                    if "arming_lanes" in existing.keys()
                    else [],
                    prior.caller_thread_id,
                    joiner_thread_id or caller_thread_id,
                )
                conn.execute(
                    "UPDATE restart_intents SET armed_at=?, expires_at=?, "
                    "wait_for_boundary=?, arming_lanes=?, updated_at=? WHERE intent_id=?",
                    (
                        armed,
                        expires,
                        1 if wfb else 0,
                        _encode_arming_lanes(merged_lanes),
                        armed,
                        existing["intent_id"],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM restart_intents WHERE intent_id=?",
                    (existing["intent_id"],),
                ).fetchone()
                conn.commit()
                out = _row_to_intent(row)
                out.deadline_at = out.deadline_at or deadline_at
                return out
            ttl = resolve_intent_ttl_s(
                wait_for_boundary=wait_for_boundary, intent_ttl_s=intent_ttl_s
            )
            agent, armed, expires = arm_stamps(
                caller_agent, now=datetime.now(UTC), ttl_s=ttl
            )
            intent_id = str(uuid.uuid4())
            now = _now()
            initial_lanes = _merge_arming_lanes([], None, caller_thread_id)
            conn.execute(
                "INSERT INTO restart_intents "
                "(intent_id, service, action, status, last_seen_event_seq, reason, "
                " park_live, wait_for_boundary, caller_agent, caller_thread_id, "
                " arming_lanes, armed_at, expires_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    intent_id,
                    service,
                    action,
                    STATUS_PENDING_DRAIN,
                    reason,
                    1 if park_live else 0,
                    1 if wait_for_boundary else 0,
                    agent,
                    (caller_thread_id or "").strip() or None,
                    _encode_arming_lanes(initial_lanes),
                    armed,
                    expires,
                    now,
                    now,
                ),
            )
            armed_reason = _reason_code("armed")
            append_status_transition(
                conn, intent_id, status=STATUS_PENDING_DRAIN, reason=armed_reason
            )
            row = conn.execute(
                "SELECT * FROM restart_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            conn.commit()
        self._update(intent_id, deadline_at=deadline_at)
        out = _row_to_intent(row)
        out.deadline_at = deadline_at
        return out

    def set_drain_epoch(
        self,
        intent_id: str,
        *,
        drain_epoch: int,
        worker_id: str,
        worker_started_at: str,
    ) -> None:
        self._update(
            intent_id,
            drain_epoch=drain_epoch,
            worker_id=worker_id,
            worker_started_at=worker_started_at,
        )

    def advance(self, intent_id: str, *, status: str, reason: str) -> None:
        if status not in _ALL_STATUSES:
            raise ValueError(f"unknown intent status: {status!r}")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            append_status_transition(conn, intent_id, status=status, reason=reason)
            conn.commit()

    def advance_if_status(
        self,
        intent_id: str,
        *,
        from_status: str,
        to_status: str,
        reason: str | None = None,
    ) -> int:
        """CAS; transition reason does not overwrite the arm reason."""
        return self.advance_if_status_in(
            intent_id,
            from_statuses=frozenset({from_status}),
            to_status=to_status,
            reason=reason,
        )

    def _refuse_aborting_status(self, status: str, to_status: str) -> bool:
        if status == STATUS_CANCELLED:
            return True
        if status == STATUS_FORCE_REQUESTED and to_status != STATUS_DRAINED_RESTARTING:
            return True
        return False

    def _advance_if_status_in_core(
        self,
        intent_id: str,
        *,
        from_statuses: frozenset[str],
        to_status: str,
        reason: str | None = None,
    ) -> int:
        recorded = _reason_code("unspecified_transition") if not reason else reason
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM restart_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if row is None:
                conn.commit()
                return 0
            current = row["status"]
            if self._refuse_aborting_status(current, to_status):
                conn.commit()
                return 0
            if current not in from_statuses:
                conn.commit()
                return 0
            changed = append_status_transition(
                conn, intent_id, status=to_status, reason=recorded
            )
            conn.commit()
            return 1 if changed else 0

    def advance_if_status_in(
        self,
        intent_id: str,
        *,
        from_statuses: frozenset[str],
        to_status: str,
        reason: str | None = None,
    ) -> int:
        """CAS from any of ``from_statuses`` (S1). One ``BEGIN IMMEDIATE`` txn."""
        if to_status not in _ALL_STATUSES:
            raise ValueError(f"unknown intent status: {to_status!r}")
        if to_status in {STATUS_CANCELLED, STATUS_FORCE_REQUESTED}:
            raise ValueError(
                f"advance_if_status_in must not target aborting status: {to_status!r}"
            )
        return self._advance_if_status_in_core(
            intent_id,
            from_statuses=from_statuses,
            to_status=to_status,
            reason=reason,
        )

    def cas_force_preempt_from_pending(
        self, intent_id: str, *, reason: str
    ) -> int:
        """CAS ``pending_drain`` → ``force_requested`` (S1 escape hatch)."""
        return self._advance_if_status_in_core(
            intent_id,
            from_statuses=frozenset({STATUS_PENDING_DRAIN}),
            to_status=STATUS_FORCE_REQUESTED,
            reason=reason,
        )

    def arm_with_boundary(
        self,
        intent_id: str,
        *,
        from_statuses: frozenset[str],
        kill_boundary_at: str,
        verify_arm_reason: str,
        completed_reason: str,
        arms_verify: bool,
    ) -> ArmResult:
        """Record kill boundary and arm verify in one txn (S2).

        Sets ``kill_boundary_at`` then appends the status transition without
        an intermediate commit. Returns ``lost_race`` when the CAS misses.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM restart_intents WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
            if row is None:
                conn.commit()
                return ArmResult(ArmResultKind.LOST_RACE)
            current = row["status"]
            target_status = (
                STATUS_VERIFYING_ACTIVATION if arms_verify else STATUS_COMPLETED
            )
            if self._refuse_aborting_status(current, target_status):
                conn.commit()
                return ArmResult(ArmResultKind.LOST_RACE)
            if current not in from_statuses:
                conn.commit()
                return ArmResult(ArmResultKind.LOST_RACE)
            stamp = _now()
            conn.execute(
                "UPDATE restart_intents SET kill_boundary_at=?, updated_at=? "
                "WHERE intent_id=?",
                (kill_boundary_at, stamp, intent_id),
            )
            if not arms_verify:
                append_status_transition(
                    conn,
                    intent_id,
                    status=STATUS_COMPLETED,
                    reason=completed_reason,
                    at=stamp,
                )
                conn.commit()
                return ArmResult(ArmResultKind.NOT_APPLICABLE)
            changed = append_status_transition(
                conn,
                intent_id,
                status=STATUS_VERIFYING_ACTIVATION,
                reason=verify_arm_reason,
                at=stamp,
            )
            conn.commit()
            if not changed:
                return ArmResult(ArmResultKind.LOST_RACE)
            return ArmResult(ArmResultKind.ARMED)

    def claim_kill(
        self,
        intent_id: str,
        *,
        worker_id: str,
        worker_started_at: str,
        drain_epoch: int,
    ) -> bool:
        """Generation-scoped kill-commit CAS (R3′).

        Exactly one winner per ``(worker_id, worker_started_at, drain_epoch)``.
        An intent already in ``drained_restarting`` for this generation is a
        successful idempotent re-drive. A loser must route to
        ``_resolve_non_kill`` — never call ``kill()``.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT intent_id FROM restart_intents
                WHERE worker_id=? AND worker_started_at=? AND drain_epoch=?
                  AND status=?
                """,
                (worker_id, worker_started_at, drain_epoch, STATUS_DRAINED_RESTARTING),
            ).fetchone()
            if existing is not None:
                return str(existing["intent_id"]) == intent_id
            try:
                matched = conn.execute(
                    """
                    SELECT status FROM restart_intents
                    WHERE intent_id=? AND status IN (?, ?)
                    """,
                    (intent_id, STATUS_PENDING_DRAIN, STATUS_TIMEOUT),
                ).fetchone()
                if matched is None:
                    return False
                changed = append_kill_commit(conn, intent_id)
            except sqlite3.IntegrityError:
                return False
            return changed == 1

    def set_kill_boundary(self, intent_id: str, *, kill_boundary_at: str) -> None:
        self._update(intent_id, kill_boundary_at=kill_boundary_at)

    def set_last_seen_seq(self, intent_id: str, seq: int) -> None:
        self._update(intent_id, last_seen_event_seq=seq)

    def set_park_summary(self, intent_id: str, *, summary: dict[str, Any]) -> None:
        """Persist the last park-for-restart sweep for operator projections."""
        self._update(intent_id, park_summary=_encode_park_summary(summary))

    def rearm(self, intent_id: str) -> Intent:
        """Refresh the expiry window. Does not change ``caller_agent``.

        A joining ``create_intent`` does this. The supervisor's 30s progress
        tick must not — an abandoned intent with only that tick expires.
        ``wait_for_boundary`` without a prior TTL stays unbounded.
        """
        from .restart_intent_expiry import arm_stamps, join_expires_at

        current = self.get(intent_id)
        if current is None:
            raise KeyError(intent_id)
        clock = datetime.now(UTC)
        _agent, armed, _ignored = arm_stamps(
            current.caller_agent, now=clock, ttl_s=None
        )
        expires = join_expires_at(
            wait_for_boundary=current.wait_for_boundary,
            intent_ttl_s=None,
            prior_expires_at=current.expires_at,
            now=clock,
        )
        self._update(intent_id, armed_at=armed, expires_at=expires)
        refreshed = self.get(intent_id)
        if refreshed is None:
            raise KeyError(intent_id)
        return refreshed

    def cancel(self, intent_id: str, *, reason: str) -> Intent:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM restart_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
            if row is None:
                raise RestartIntentCancelError(
                    f"restart intent not found: {intent_id!r}"
                )
            current = _row_to_intent(row)
            if current.status == STATUS_CANCELLED:
                return current
            if current.status != STATUS_PENDING_DRAIN:
                raise RestartIntentCancelError(
                    f"cancel refused: status={current.status!r}",
                    status=current.status,
                )
            append_status_transition(
                conn, intent_id, status=STATUS_CANCELLED, reason=reason
            )
            row = conn.execute(
                "SELECT * FROM restart_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        return _row_to_intent(row)

    def _update(self, intent_id: str, **fields: Any) -> None:
        cols = [*fields.keys(), "updated_at"]
        vals = [*fields.values(), _now()]
        assignments = ", ".join(f"{c}=?" for c in cols)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE restart_intents SET {assignments} WHERE intent_id=?",
                (*vals, intent_id),
            )

    def get(self, intent_id: str) -> Intent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM restart_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        return _row_to_intent(row) if row is not None else None

    def pending_intents(self) -> list[Intent]:
        placeholders = ",".join("?" * len(_NEEDS_RECONCILE))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM restart_intents WHERE status IN ({placeholders}) "
                "ORDER BY created_at",
                _NEEDS_RECONCILE,
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def intents_with_status(self, status: str) -> list[Intent]:
        """All rows in one status, oldest first (timeout-gap repair feed)."""
        if status not in _ALL_STATUSES:
            raise ValueError(f"unknown intent status: {status!r}")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM restart_intents WHERE status=? ORDER BY created_at",
                (status,),
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def active_for_service(self, service: str) -> Intent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM restart_intents WHERE service=? AND status IN "
                f"({','.join('?' * len(_BLOCKS_NEW_RESTART))}) ORDER BY created_at LIMIT 1",
                (service, *_BLOCKS_NEW_RESTART),
            ).fetchone()
        return _row_to_intent(row) if row is not None else None

    def open_window(self, **kwargs: Any) -> RestartWindow:
        return self._windows.open_window(**kwargs)

    def clear_window(self, window_id: str) -> RestartWindow | None:
        return self._windows.clear_window(window_id)

    def clear_open_for_service(self, service: str) -> list[RestartWindow]:
        return self._windows.clear_open_for_service(service)

    def clear_open_fleet_windows(self) -> list[RestartWindow]:
        return self._windows.clear_open_fleet_windows()

    def sweep_expired_windows(
        self, *, now: datetime | None = None
    ) -> list[RestartWindow]:
        return self._windows.sweep_expired_windows(now=now)

    def active_windows(self) -> list[RestartWindow]:
        return self._windows.active_windows()

    def window_for_service(
        self, service: str, *, now: datetime | None = None
    ) -> RestartWindow | None:
        return self._windows.window_for_service(service, now=now)

    def restart_window_projection(self, *, now: datetime | None = None) -> dict:
        return self._windows.projection(now=now)

    def restart_window_for_service(
        self, service: str, *, now: datetime | None = None
    ) -> RestartWindowView | None:
        return self._windows.service_projection(service, now=now)

    def coalesce_owed_wake_milestone(
        self,
        *,
        holder_id: str,
        lane_thread_id: str | None,
        turn_ref: dict[str, Any],
    ) -> tuple[OwedWakeRow, bool]:
        """At most one open owed_wake row per holder; append turn_ref when coalescing."""
        from .owed_wake_ledger import (
            PRODUCER_MILESTONE,
            decode_turn_refs,
            encode_turn_refs,
        )

        hid = (holder_id or "").strip()
        if not hid:
            raise ValueError("holder_id required")
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM owed_wake WHERE holder_id=? AND closed_at IS NULL",
                (hid,),
            ).fetchone()
            if existing is not None:
                refs = decode_turn_refs(existing["turn_refs"])
                refs.append(turn_ref)
                conn.execute(
                    "UPDATE owed_wake SET turn_refs=?, updated_at=? WHERE row_id=?",
                    (encode_turn_refs(refs), now, existing["row_id"]),
                )
                conn.commit()
                refreshed = conn.execute(
                    "SELECT * FROM owed_wake WHERE row_id=?", (existing["row_id"],)
                ).fetchone()
                return self._row_to_owed_wake(refreshed), False
            row_id = str(uuid.uuid4())
            refs = [turn_ref]
            conn.execute(
                "INSERT INTO owed_wake "
                "(row_id, holder_id, producer, lane_thread_id, turn_refs, "
                " arming_lanes, restart_intent_id, dispatched_at, closed_at, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, '[]', NULL, NULL, NULL, ?, ?)",
                (
                    row_id,
                    hid,
                    PRODUCER_MILESTONE,
                    (lane_thread_id or "").strip() or None,
                    encode_turn_refs(refs),
                    now,
                    now,
                ),
            )
            conn.commit()
        row = self.get_owed_wake(row_id)
        assert row is not None
        return row, True

    def create_owed_wake_drain_complete(
        self,
        *,
        holder_id: str,
        restart_intent_id: str,
        arming_lanes: list[str],
        lane_thread_id: str | None,
        turn_ref: dict[str, Any],
    ) -> tuple[OwedWakeRow, bool]:
        """At most one open owed_wake row per holder; append turn_ref when coalescing."""
        from .owed_wake_ledger import (
            PRODUCER_DRAIN_COMPLETE,
            decode_turn_refs,
            encode_turn_refs,
        )

        hid = (holder_id or "").strip()
        if not hid:
            raise ValueError("holder_id required")
        now = _now()
        lanes_json = _encode_arming_lanes(arming_lanes) or "[]"
        lane = (lane_thread_id or "").strip() or None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM owed_wake WHERE holder_id=? AND closed_at IS NULL",
                (hid,),
            ).fetchone()
            if existing is not None:
                refs = decode_turn_refs(existing["turn_refs"])
                refs.append(turn_ref)
                conn.execute(
                    "UPDATE owed_wake SET turn_refs=?, arming_lanes=?, "
                    "restart_intent_id=?, lane_thread_id=COALESCE(?, lane_thread_id), "
                    "updated_at=? WHERE row_id=?",
                    (
                        encode_turn_refs(refs),
                        lanes_json,
                        restart_intent_id,
                        lane,
                        now,
                        existing["row_id"],
                    ),
                )
                conn.commit()
                refreshed = conn.execute(
                    "SELECT * FROM owed_wake WHERE row_id=?", (existing["row_id"],)
                ).fetchone()
                return self._row_to_owed_wake(refreshed), False
            row_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO owed_wake "
                "(row_id, holder_id, producer, lane_thread_id, turn_refs, "
                " arming_lanes, restart_intent_id, dispatched_at, closed_at, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (
                    row_id,
                    hid,
                    PRODUCER_DRAIN_COMPLETE,
                    lane,
                    encode_turn_refs([turn_ref]),
                    lanes_json,
                    restart_intent_id,
                    now,
                    now,
                ),
            )
            conn.commit()
        row = self.get_owed_wake(row_id)
        assert row is not None
        return row, True

    def get_owed_wake(self, row_id: str) -> OwedWakeRow | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM owed_wake WHERE row_id=?", (row_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_owed_wake(row)

    def list_open_undispatched_owed_wakes(self) -> list[OwedWakeRow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM owed_wake WHERE closed_at IS NULL "
                "AND dispatched_at IS NULL ORDER BY created_at"
            ).fetchall()
        return [self._row_to_owed_wake(r) for r in rows]

    def close_owed_wake_for_holder(self, holder_id: str) -> int:
        now = _now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE owed_wake SET closed_at=?, updated_at=? "
                "WHERE holder_id=? AND closed_at IS NULL",
                (now, now, holder_id),
            )
            conn.commit()
            return int(cur.rowcount)

    def mark_owed_wake_dispatched(self, row_id: str, *, outcome: str) -> None:
        terminal = outcome in {"dispatched", "held_no_live_seat", "skipped"}
        stamp = _now() if terminal else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE owed_wake SET dispatched_at=?, updated_at=? WHERE row_id=?",
                (stamp, _now(), row_id),
            )

    def _row_to_owed_wake(
        self, row: sqlite3.Row, *, turn_refs: list[dict[str, Any]] | None = None
    ) -> OwedWakeRow:
        from .owed_wake_ledger import OwedWakeRow, decode_arming_lanes, decode_turn_refs

        refs = turn_refs if turn_refs is not None else decode_turn_refs(row["turn_refs"])
        return OwedWakeRow(
            row_id=row["row_id"],
            holder_id=row["holder_id"],
            producer=row["producer"],
            lane_thread_id=row["lane_thread_id"],
            turn_refs=refs,
            arming_lanes=decode_arming_lanes(row["arming_lanes"]),
            restart_intent_id=row["restart_intent_id"],
            dispatched_at=row["dispatched_at"],
            closed_at=row["closed_at"],
        )


def _merge_arming_lanes(
    existing: list[str] | None,
    prior_caller_thread: str | None,
    new_lane: str | None,
) -> list[str]:
    out: list[str] = []
    for candidate in (
        *(existing or []),
        (prior_caller_thread or "").strip(),
        (new_lane or "").strip(),
    ):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


# Re-export status constants for backward compatibility.
from .restart_intent_states import (  # noqa: E402
    STATUS_ACTIVATION_UNVERIFIED,
)

__all__ = [
    "ArmResult",
    "ArmResultKind",
    "LIVE_STATUSES",
    "Intent",
    "IntentStatusView",
    "RestartIntentCancelError",
    "RestartIntentStore",
    "STATUS_ACTIVATION_UNVERIFIED",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_DRAINED_RESTARTING",
    "STATUS_FAILED",
    "STATUS_FORCE_REQUESTED",
    "STATUS_PENDING_DRAIN",
    "STATUS_TIMEOUT",
    "STATUS_VERIFYING_ACTIVATION",
    "intent_status_view",
]
