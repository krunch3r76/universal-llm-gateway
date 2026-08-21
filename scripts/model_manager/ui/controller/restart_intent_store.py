"""Durable restart-intent state for manage-initiated deferred restarts (R-D).

SQLite-backed (mirrors the cursor-dispatch ledger pattern: a small WAL db under
the manage data dir, ``GATEWAY_DIR`` = ``~/.gateway``). One row per restart
intent. ``_BLOCKS_NEW_RESTART`` statuses coalesce concurrent restarts; a partial
unique index backstops that predicate. ``_NEEDS_RECONCILE`` is the boot feed and
also includes ``verifying_activation`` so activation proof survives manage
restart without a second begin-drain.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from universal_logging import get_logger

from .restart_intent_migrate import _DDL, apply_restart_intent_schema
from .restart_intent_states import (
    _ALL_STATUSES,
    _BLOCKS_NEW_RESTART,
    _NEEDS_RECONCILE,
    _TERMINAL,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_DRAINED_RESTARTING,
    STATUS_FAILED,
    STATUS_FORCE_REQUESTED,
    STATUS_PENDING_DRAIN,
    STATUS_TIMEOUT,
)
from .restart_window_store import (
    RestartWindow,
    RestartWindowStore,
    RestartWindowView,
)
from .service_config import GATEWAY_DIR

logger = get_logger(__name__)


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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


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
        self, *, service: str, action: str, deadline_at: str, reason: str
    ) -> Intent:
        """INSERT ``pending_drain``, or return existing if status blocks new restart."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM restart_intents WHERE service=? AND status IN "
                f"({','.join('?' * len(_BLOCKS_NEW_RESTART))}) ORDER BY created_at LIMIT 1",
                (service, *_BLOCKS_NEW_RESTART),
            ).fetchone()
            if existing is not None:
                return _row_to_intent(existing)
            intent_id = str(uuid.uuid4())
            now = _now()
            conn.execute(
                "INSERT INTO restart_intents "
                "(intent_id, service, action, status, last_seen_event_seq, reason, "
                " created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?)",
                (intent_id, service, action, STATUS_PENDING_DRAIN, reason, now, now),
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

    def advance(self, intent_id: str, *, status: str) -> None:
        if status not in _ALL_STATUSES:
            raise ValueError(f"unknown intent status: {status!r}")
        self._update(intent_id, status=status)

    def advance_if_status(
        self,
        intent_id: str,
        *,
        from_status: str,
        to_status: str,
        reason: str | None = None,
    ) -> int:
        """CAS status transition; returns rowcount (0 when already moved)."""
        if to_status not in _ALL_STATUSES:
            raise ValueError(f"unknown intent status: {to_status!r}")
        now = _now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE restart_intents
                SET status=?, reason=COALESCE(?, reason), updated_at=?
                WHERE intent_id=? AND status=?
                """,
                (to_status, reason, now, intent_id, from_status),
            )
            return int(cursor.rowcount)

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
        now = _now()
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
                cursor = conn.execute(
                    """
                    UPDATE restart_intents
                    SET status=?, updated_at=?
                    WHERE intent_id=? AND status IN (?, ?)
                    """,
                    (
                        STATUS_DRAINED_RESTARTING,
                        now,
                        intent_id,
                        STATUS_PENDING_DRAIN,
                        STATUS_TIMEOUT,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
            return int(cursor.rowcount) == 1

    def set_kill_boundary(self, intent_id: str, *, kill_boundary_at: str) -> None:
        self._update(intent_id, kill_boundary_at=kill_boundary_at)

    def set_last_seen_seq(self, intent_id: str, seq: int) -> None:
        self._update(intent_id, last_seen_event_seq=seq)

    def cancel(self, intent_id: str) -> Intent:
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
            now = _now()
            conn.execute(
                "UPDATE restart_intents SET status=?, updated_at=? WHERE intent_id=?",
                (STATUS_CANCELLED, now, intent_id),
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


# Re-export status constants for backward compatibility.
from .restart_intent_states import (  # noqa: E402
    STATUS_ACTIVATION_UNVERIFIED,
    STATUS_VERIFYING_ACTIVATION,
)

__all__ = [
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
