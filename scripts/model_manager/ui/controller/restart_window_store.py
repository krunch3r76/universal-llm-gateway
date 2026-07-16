"""Operator-authored restart windows — durable SOT rows in restart-intents.db.

Complements git-worker drain intents in ``restart_intent_store`` (same DB file,
separate table). A window is opened before the first stop of an operator-initiated
lifecycle op and cleared on healthy / fleet completion / TTL — never inferred from
reachability alone.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

SCOPE_SERVICE = "service"
SCOPE_FLEET = "fleet"
STATE_OPEN = "open"
STATE_CLEARED = "cleared"

DEFAULT_SERVICE_TTL_S = 180
DEFAULT_FLEET_TTL_S = 600
RETRY_AFTER_S = 30

FLEET_WINDOW_SERVICES: tuple[str, ...] = (
    "gateway",
    "stargate",
    "rag",
    "cloud_proxy",
    "mcp",
    "event_service",
    "cortex_api",
    "agent_bus",
    "git_integration_worker",
    "email_bridge",
)

_WINDOW_DDL = """
CREATE TABLE IF NOT EXISTS restart_windows (
    window_id    TEXT PRIMARY KEY,
    scope        TEXT NOT NULL CHECK (scope IN ('service', 'fleet')),
    service_set  TEXT NOT NULL,
    state        TEXT NOT NULL CHECK (state IN ('open', 'cleared')),
    opened_at    TEXT NOT NULL,
    deadline_at  TEXT NOT NULL,
    cleared_at   TEXT,
    reason       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_restart_windows_open
    ON restart_windows(state)
    WHERE state = 'open';
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@dataclass(slots=True)
class RestartWindow:
    window_id: str
    scope: str
    service_set: list[str]
    state: str
    opened_at: str
    deadline_at: str
    cleared_at: str | None
    reason: str | None
    created_at: str
    updated_at: str

    @property
    def is_open(self) -> bool:
        return self.state == STATE_OPEN

    def covers_service(self, service: str) -> bool:
        return service in self.service_set


class RestartWindowView(TypedDict):
    window_id: str
    scope: str
    service_set: list[str]
    state: str
    opened_at: str
    deadline_at: str
    retry_after_s: int
    elapsed_s: int


def window_status_view(window: RestartWindow, *, now: datetime) -> RestartWindowView:
    opened = _parse_ts(window.opened_at)
    return {
        "window_id": window.window_id,
        "scope": window.scope,
        "service_set": list(window.service_set),
        "state": window.state,
        "opened_at": window.opened_at,
        "deadline_at": window.deadline_at,
        "retry_after_s": RETRY_AFTER_S,
        "elapsed_s": round((now - opened).total_seconds()),
    }


def _row_to_window(row: sqlite3.Row) -> RestartWindow:
    raw = row["service_set"]
    service_set = json.loads(raw) if isinstance(raw, str) else list(raw)
    return RestartWindow(
        window_id=row["window_id"],
        scope=row["scope"],
        service_set=service_set,
        state=row["state"],
        opened_at=row["opened_at"],
        deadline_at=row["deadline_at"],
        cleared_at=row["cleared_at"],
        reason=row["reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class RestartWindowStore:
    """Filesystem-backed restart-window rows (SQLite under GATEWAY_DIR)."""

    def __init__(self, db_path: Path, connect: Any) -> None:
        self._db_path = db_path
        self._connect = connect
        with self._connect() as conn:
            conn.executescript(_WINDOW_DDL)

    def open_window(
        self,
        *,
        scope: str,
        service_set: list[str],
        deadline_at: str,
        reason: str,
    ) -> RestartWindow:
        if scope not in (SCOPE_SERVICE, SCOPE_FLEET):
            raise ValueError(f"unknown window scope: {scope!r}")
        if not service_set:
            raise ValueError("service_set must be non-empty")
        window_id = str(uuid.uuid4())
        now = _now()
        payload = json.dumps(sorted(set(service_set)))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO restart_windows "
                "(window_id, scope, service_set, state, opened_at, deadline_at, "
                " reason, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    window_id,
                    scope,
                    payload,
                    STATE_OPEN,
                    now,
                    deadline_at,
                    reason,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM restart_windows WHERE window_id=?", (window_id,)
            ).fetchone()
        return _row_to_window(row)

    def clear_window(self, window_id: str) -> RestartWindow | None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE restart_windows SET state=?, cleared_at=?, updated_at=? "
                "WHERE window_id=? AND state=?",
                (STATE_CLEARED, now, now, window_id, STATE_OPEN),
            )
            row = conn.execute(
                "SELECT * FROM restart_windows WHERE window_id=?", (window_id,)
            ).fetchone()
        return _row_to_window(row) if row is not None else None

    def clear_open_for_service(self, service: str) -> list[RestartWindow]:
        cleared: list[RestartWindow] = []
        for window in self.active_windows():
            if window.covers_service(service):
                out = self.clear_window(window.window_id)
                if out is not None:
                    cleared.append(out)
        return cleared

    def clear_open_fleet_windows(self) -> list[RestartWindow]:
        cleared: list[RestartWindow] = []
        for window in self.active_windows():
            if window.scope == SCOPE_FLEET:
                out = self.clear_window(window.window_id)
                if out is not None:
                    cleared.append(out)
        return cleared

    def sweep_expired_windows(self, *, now: datetime | None = None) -> list[RestartWindow]:
        now = now or datetime.now(UTC)
        expired: list[RestartWindow] = []
        for window in self.active_windows():
            if _parse_ts(window.deadline_at) <= now:
                out = self.clear_window(window.window_id)
                if out is not None:
                    expired.append(out)
        return expired

    def active_windows(self) -> list[RestartWindow]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM restart_windows WHERE state=? ORDER BY opened_at",
                (STATE_OPEN,),
            ).fetchall()
        return [_row_to_window(r) for r in rows]

    def window_for_service(self, service: str, *, now: datetime | None = None) -> RestartWindow | None:
        now = now or datetime.now(UTC)
        self.sweep_expired_windows(now=now)
        for window in self.active_windows():
            if window.covers_service(service) and _parse_ts(window.deadline_at) > now:
                return window
        return None

    def projection(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        self.sweep_expired_windows(now=now)
        open_windows = [
            window_status_view(w, now=now) for w in self.active_windows()
        ]
        return {"open": open_windows}

    def service_projection(
        self, service: str, *, now: datetime | None = None
    ) -> RestartWindowView | None:
        window = self.window_for_service(service, now=now)
        if window is None:
            return None
        return window_status_view(window, now=now or datetime.now(UTC))
