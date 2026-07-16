"""Durable restart-intent state for manage-initiated deferred restarts (R-D).

SQLite-backed (mirrors the cursor-dispatch ledger pattern: a small WAL db under
the manage data dir, ``GATEWAY_DIR`` = ``~/.gateway``). One row per restart
intent. The store is the single update path per intent; the existing per-service
``FifoCapacityGate(limit=1)`` restart mutex coalesces concurrent restarts, and a
partial unique index backstops "one live non-terminal intent per service".

Phase 2 of ``task:git-worker-event-driven-drain`` (manage side). The supervisor
(``git_worker_drain_supervisor.py``) consumes these rows; ``pending_intents`` is
the startup-reconcile feed so an event-driven restart survives a manage restart.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from universal_logging import get_logger

from .restart_window_store import (
    DEFAULT_FLEET_TTL_S,
    DEFAULT_SERVICE_TTL_S,
    FLEET_WINDOW_SERVICES,
    RETRY_AFTER_S,
    RestartWindow,
    RestartWindowStore,
    RestartWindowView,
    window_status_view,
)
from .service_config import GATEWAY_DIR

logger = get_logger(__name__)

# Intent lifecycle states.
STATUS_PENDING_DRAIN = "pending_drain"
STATUS_DRAINED_RESTARTING = "drained_restarting"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_FORCE_REQUESTED = "force_requested"
STATUS_CANCELLED = "cancelled"

_NON_TERMINAL = (STATUS_PENDING_DRAIN, STATUS_DRAINED_RESTARTING)
_TERMINAL = frozenset(
    {
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_TIMEOUT,
        STATUS_FORCE_REQUESTED,
        STATUS_CANCELLED,
    }
)
_ALL_STATUSES = frozenset(_NON_TERMINAL) | _TERMINAL

_DDL = """
CREATE TABLE IF NOT EXISTS restart_intents (
    intent_id            TEXT PRIMARY KEY,
    service              TEXT NOT NULL,
    action               TEXT NOT NULL DEFAULT 'restart',
    status               TEXT NOT NULL CHECK (status IN (
        'pending_drain','drained_restarting','completed',
        'failed','timeout','force_requested','cancelled')),
    drain_epoch          INTEGER,
    worker_id            TEXT,
    worker_started_at    TEXT,
    deadline_at          TEXT,
    last_seen_event_seq  INTEGER NOT NULL DEFAULT 0,
    reason               TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);
-- One live (non-terminal) intent per service. Backstops the check-then-insert in
-- create_intent (the restart-mutex already serialises concurrent creates on-loop).
CREATE UNIQUE INDEX IF NOT EXISTS idx_restart_intent_live
    ON restart_intents(service)
    WHERE status IN ('pending_drain','drained_restarting');
"""


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
    created_at: str
    updated_at: str

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL


class IntentStatusView(TypedDict):
    restart_intent_id: str
    status: str
    drain_epoch: int | None
    deadline_at: str | None
    elapsed_s: int


def intent_status_view(intent: Intent, *, now: datetime) -> IntentStatusView:
    """Five-field live-intent projection shared by busy_status and the TUI."""
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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class RestartIntentStore:
    """Durable singleton for restart intents. DB methods are synchronous.

    Mirrors ``CursorDispatchLedger``: the path is captured once at construction
    and the singleton is test-resettable via ``RestartIntentStore._instance =
    None`` (or by constructing with an explicit ``db_path``).
    """

    _instance: RestartIntentStore | None = None

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path: Path = db_path or _default_path()
        with self._connect() as conn:
            conn.executescript(_DDL)
        self._windows = RestartWindowStore(self._db_path, self._connect)

    def _connect(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    @classmethod
    def instance(cls) -> RestartIntentStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # --------------------------------------------------------------- mutations
    def create_intent(
        self, *, service: str, action: str, deadline_at: str, reason: str
    ) -> Intent:
        """INSERT a ``pending_drain`` intent, or return the existing live one.

        Enforces one live non-terminal intent per service (idempotent coalescing,
        AC-6): a create while one is ``pending_drain``/``drained_restarting``
        returns the existing intent and does NOT insert a second row.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM restart_intents WHERE service=? AND status IN "
                "(?, ?) ORDER BY created_at LIMIT 1",
                (service, *_NON_TERMINAL),
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
        # deadline_at is set separately so the create stays a single INSERT shape;
        # apply it now via advance-style update.
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

    def set_last_seen_seq(self, intent_id: str, seq: int) -> None:
        self._update(intent_id, last_seen_event_seq=seq)

    def _update(self, intent_id: str, **fields: Any) -> None:
        """Patch the named columns + bump ``updated_at`` in one statement."""
        cols = [*fields.keys(), "updated_at"]
        vals = [*fields.values(), _now()]
        assignments = ", ".join(f"{c}=?" for c in cols)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE restart_intents SET {assignments} WHERE intent_id=?",
                (*vals, intent_id),
            )

    # ------------------------------------------------------------------- reads
    def get(self, intent_id: str) -> Intent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM restart_intents WHERE intent_id=?", (intent_id,)
            ).fetchone()
        return _row_to_intent(row) if row is not None else None

    def pending_intents(self) -> list[Intent]:
        """Non-terminal rows, oldest first — the startup-reconcile feed."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM restart_intents WHERE status IN (?, ?) "
                "ORDER BY created_at",
                _NON_TERMINAL,
            ).fetchall()
        return [_row_to_intent(r) for r in rows]

    def active_for_service(self, service: str) -> Intent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM restart_intents WHERE service=? AND status IN "
                "(?, ?) ORDER BY created_at LIMIT 1",
                (service, *_NON_TERMINAL),
            ).fetchone()
        return _row_to_intent(row) if row is not None else None

    # ---------------------------------------------------------- restart windows
    def open_window(
        self,
        *,
        scope: str,
        service_set: list[str],
        deadline_at: str,
        reason: str,
    ) -> RestartWindow:
        return self._windows.open_window(
            scope=scope,
            service_set=service_set,
            deadline_at=deadline_at,
            reason=reason,
        )

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
