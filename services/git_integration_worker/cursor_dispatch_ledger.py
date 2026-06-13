"""Durable, restart-surviving idempotency + resume ledger for cursor-sdk dispatches.

Replaces the process-local CursorDispatchRegistry. SQLite at DATA_DIR/cursor-sdk-dispatch.db
(mirrors event_store.dispatch_journal). The ledger answers "what SHOULD be running";
a process-local task dict answers "what IS running in this process". Never sole authority:
a lost row degrades to Phase-1 loud-failure (thread_dispatch_links + agent-bus reconciler).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from services.git_integration_worker.models.cursor_api import (
    CursorDispatchRequest,
    CursorDispatchResponse,
)

logger = get_logger(__name__)

_STATUS_ADMITTED = "admitted"
_STATUS_RUNNING = "running"
_STATUS_TERMINAL = ("completed", "failed")

_DDL = """
CREATE TABLE IF NOT EXISTS cursor_sdk_dispatches (
    dispatch_id        TEXT PRIMARY KEY,
    fingerprint        TEXT NOT NULL,
    thread_id          TEXT NOT NULL,
    execution_id       TEXT,
    resolved_model     TEXT NOT NULL,
    packet_path        TEXT,
    message_present    INTEGER NOT NULL DEFAULT 0,
    state_root         TEXT,
    sdk_agent_id       TEXT,
    sdk_run_id         TEXT,
    status             TEXT NOT NULL CHECK (status IN ('admitted','running','completed','failed')),
    started_at         TEXT,
    last_heartbeat_at  TEXT,
    terminal_status    TEXT CHECK (terminal_status IN ('completed','failed')),
    terminal_at        TEXT,
    record_json        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_running
    ON cursor_sdk_dispatches(status) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_execution
    ON cursor_sdk_dispatches(execution_id);
"""


class DispatchConflict(Exception):  # noqa: N818 — spec name (preserved from registry)
    """Raised when dispatch_id fingerprint does not match a prior admission."""


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


def _ledger_path() -> Path:
    data_dir = Path(os.getenv("DATA_DIR", str(Path.home() / ".gateway"))).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "cursor-sdk-dispatch.db"


def _connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or _ledger_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@dataclass(frozen=True, slots=True)
class LedgerRow:
    dispatch_id: str
    thread_id: str
    execution_id: str | None
    caller_agent: str | None
    resolved_model: str
    state_root: str | None
    sdk_agent_id: str | None
    sdk_run_id: str | None
    status: str
    started_at: str | None
    last_heartbeat_at: str | None


class CursorDispatchLedger:
    """Durable singleton; survives worker restart. DB methods are sync (F1)."""

    _instance: CursorDispatchLedger | None = None

    def __init__(self) -> None:
        # Live in-process task handles (NOT persistable): dispatch_id -> Task.
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        # Capture the ledger DB path ONCE at construction (happens pre-HOME-swap,
        # at record_state_root). The dispatch path swaps os.environ["HOME"] for
        # cursor-sdk-bridge isolation, and _ledger_path() falls back to
        # Path.home() when DATA_DIR is unset, so re-resolving per _connect()
        # would aim in-swap ops (heartbeat, sdk_identity, terminal) at an empty
        # <swapped-home>/.gateway DB -> "no such table: cursor_sdk_dispatches".
        self._db_path: Path = _ledger_path()
        with self._connect() as conn:
            conn.executescript(_DDL)
            cols = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(cursor_sdk_dispatches)")
            }
            if "caller_agent" not in cols:
                conn.execute(
                    "ALTER TABLE cursor_sdk_dispatches ADD COLUMN caller_agent TEXT"
                )

    def _connect(self) -> sqlite3.Connection:
        return _connect(self._db_path)

    @classmethod
    def instance(cls) -> CursorDispatchLedger:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def fingerprint(req: CursorDispatchRequest) -> str:
        payload = {
            "thread_id": req.thread_id,
            "model": req.model,
            "dispatch_id": req.dispatch_id,
            "execution_id": req.execution_id,
            "packet_path": req.packet_path,
            "message": req.message,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def admit(
        self,
        *,
        req: CursorDispatchRequest,
        fingerprint: str,
        execution_id: str | None,
        caller_agent: str | None,
        resolved_model: str,
        admission: CursorDispatchResponse,
    ) -> CursorDispatchResponse | None:
        """Durable idempotency (F2). Returns cached admission on hit, None on first admit.
        Raises DispatchConflict on fingerprint mismatch."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT fingerprint FROM cursor_sdk_dispatches WHERE dispatch_id = ?",
                (req.dispatch_id,),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise DispatchConflict(
                        f"dispatch_id {req.dispatch_id!r} already admitted with "
                        "different payload fingerprint"
                    )
                return admission  # idempotent hit (now restart-durable)
            conn.execute(
                "INSERT INTO cursor_sdk_dispatches "
                "(dispatch_id, fingerprint, thread_id, execution_id, caller_agent, "
                " resolved_model, packet_path, message_present, status, record_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    req.dispatch_id,
                    fingerprint,
                    req.thread_id,
                    execution_id,
                    caller_agent,
                    resolved_model,
                    req.packet_path,
                    1 if req.message else 0,
                    _STATUS_ADMITTED,
                    "{}",
                ),
            )
        return None

    def mark_running(self, *, dispatch_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, started_at=? "
                "WHERE dispatch_id=? AND status=?",
                (_STATUS_RUNNING, _now(), dispatch_id, _STATUS_ADMITTED),
            )

    def record_state_root(self, *, dispatch_id: str, state_root: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET state_root=? WHERE dispatch_id=?",
                (state_root, dispatch_id),
            )

    def record_sdk_identity(
        self, *, dispatch_id: str, agent_id: str | None, run_id: str | None
    ) -> None:
        """F3: capture whatever the SDK exposes; columns nullable."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET sdk_agent_id=?, sdk_run_id=? "
                "WHERE dispatch_id=?",
                (agent_id, run_id, dispatch_id),
            )

    def bump_heartbeat(self, *, dispatch_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET last_heartbeat_at=? WHERE dispatch_id=?",
                (_now(), dispatch_id),
            )

    def mark_terminal(self, *, dispatch_id: str, terminal_status: str) -> None:
        assert terminal_status in _STATUS_TERMINAL
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, terminal_status=?, terminal_at=? "
                "WHERE dispatch_id=?",
                (terminal_status, terminal_status, _now(), dispatch_id),
            )

    def running_orphans(self) -> list[LedgerRow]:
        """status='running' rows with NO live local task (restart survivors)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dispatch_id, thread_id, execution_id, caller_agent, resolved_model, "
                "state_root, sdk_agent_id, sdk_run_id, status, started_at, last_heartbeat_at "
                "FROM cursor_sdk_dispatches WHERE status='running'"
            ).fetchall()
        out: list[LedgerRow] = []
        for r in rows:
            if (
                self._tasks.get(r["dispatch_id"]) is not None
                and not self._tasks[r["dispatch_id"]].done()
            ):
                continue
            out.append(LedgerRow(**{k: r[k] for k in r.keys()}))
        return out

    def register_task(self, dispatch_id: str, task: asyncio.Task[Any]) -> None:
        self._tasks[dispatch_id] = task

    def active_snapshot(self) -> dict[str, Any]:
        """Live-task-aware occupancy for the integrate restart-defer gate.

        Mirrors the deleted registry: a 'running' DB row with no live local
        task is an orphan (restart survivor) and is excluded so a crashed
        dispatch cannot wedge the gate. Use ``running_orphans`` for the
        reconciler view of survivors.
        """
        with self._connect() as conn:
            ids = [
                row["dispatch_id"]
                for row in conn.execute(
                    "SELECT dispatch_id FROM cursor_sdk_dispatches WHERE status='running'"
                ).fetchall()
            ]
        live = [
            d for d in ids if (t := self._tasks.get(d)) is not None and not t.done()
        ]
        return {"running": len(live), "dispatch_ids": live}
