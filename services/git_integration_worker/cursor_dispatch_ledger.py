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

_STATUS_QUEUED = "queued"
_STATUS_ADMITTED = "admitted"
_STATUS_RUNNING = "running"
_STATUS_PARKED_WAITING = "parked_waiting"
_STATUS_TERMINAL = ("completed", "failed")
_ACTIVE_WRITER_STATUSES = (_STATUS_ADMITTED, _STATUS_RUNNING)
_STATUS_CHECK = (
    "'queued','admitted','running','parked_waiting','completed','failed'"
)

_DDL = """
CREATE TABLE IF NOT EXISTS cursor_sdk_dispatches (
    dispatch_id        TEXT PRIMARY KEY,
    fingerprint        TEXT NOT NULL,
    thread_id          TEXT NOT NULL,
    execution_id       TEXT,
    caller_agent       TEXT,
    resolved_model     TEXT NOT NULL,
    packet_path        TEXT,
    message_present    INTEGER NOT NULL DEFAULT 0,
    state_root         TEXT,
    sdk_agent_id       TEXT,
    sdk_run_id         TEXT,
    status             TEXT NOT NULL CHECK (status IN ('queued','admitted','running','parked_waiting','completed','failed')),
    started_at         TEXT,
    last_heartbeat_at  TEXT,
    terminal_status    TEXT CHECK (terminal_status IN ('completed','failed')),
    terminal_at        TEXT,
    record_json        TEXT NOT NULL DEFAULT '{}',
    queued_at          TEXT,
    park_child_dispatch_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_running
    ON cursor_sdk_dispatches(status) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_execution
    ON cursor_sdk_dispatches(execution_id);
"""


class DispatchConflict(Exception):  # noqa: N818 — spec name (preserved from registry)
    """Raised when dispatch_id fingerprint does not match a prior admission."""


class SourceRefConflict(Exception):  # noqa: N818 — peer implement gate
    """Raised when a non-terminal implement already holds the same ``source_ref``.

    Attributes:
        source_ref: Work-item identity that collided.
        holder_dispatch_id: In-flight peer dispatch id.
        holder_thread_id: In-flight peer bus thread id, when persisted.
    """

    def __init__(
        self,
        *,
        source_ref: str,
        holder_dispatch_id: str,
        holder_thread_id: str | None,
    ) -> None:
        self.source_ref = source_ref
        self.holder_dispatch_id = holder_dispatch_id
        self.holder_thread_id = holder_thread_id
        super().__init__(
            f"source_ref {source_ref!r} already has non-terminal implement "
            f"(holder_dispatch_id={holder_dispatch_id!r}, "
            f"holder_thread_id={holder_thread_id!r})"
        )


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
    source_repo: str | None = None
    contract: str | None = None
    read_only: bool = False
    record_json: str = "{}"


@dataclass(frozen=True, slots=True)
class PromotedDispatch:
    """FIFO head promoted from queued → admitted."""

    dispatch_id: str
    thread_id: str
    execution_id: str | None
    caller_agent: str | None
    resolved_model: str
    source_repo: str | None
    contract: str | None
    read_only: bool
    record_json: str


def _dispatch_record_json(req: CursorDispatchRequest) -> str:
    payload = {
        "model": req.model,
        "message": req.message,
        "packet_path": req.packet_path,
        "handoff_contract": req.handoff_contract,
        "prompt_preamble": req.prompt_preamble,
        "model_knobs": req.model_knobs,
        "read_only": req.read_only,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _subject_preview(record_json: str, packet_path: str | None = None) -> str | None:
    try:
        data = json.loads(record_json) if record_json else {}
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    message = data.get("message")
    if isinstance(message, str) and message.strip():
        first_line = message.strip().splitlines()[0].strip()
        if first_line:
            return first_line[:120]
    path = data.get("packet_path") or packet_path
    if isinstance(path, str) and path.strip():
        return Path(path.strip()).name[:120]
    return None


def _holder_projection(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "holder_dispatch_id": None,
            "holder_thread_id": None,
            "holder_resolved_model": None,
            "holder_subject_preview": None,
            "holder_status": None,
            "holder_started_at": None,
            "holder_last_heartbeat_at": None,
        }
    record_json = row["record_json"] if "record_json" in row.keys() else "{}"
    packet_path = row["packet_path"] if "packet_path" in row.keys() else None
    return {
        "holder_dispatch_id": row["dispatch_id"],
        "holder_thread_id": row["thread_id"],
        "holder_resolved_model": row["resolved_model"],
        "holder_subject_preview": _subject_preview(record_json or "{}", packet_path),
        "holder_status": row["status"],
        "holder_started_at": row["started_at"] if "started_at" in row.keys() else None,
        "holder_last_heartbeat_at": (
            row["last_heartbeat_at"] if "last_heartbeat_at" in row.keys() else None
        ),
    }


def _fetch_active_holder_conn(
    conn: sqlite3.Connection, *, source_repo: str | None
) -> sqlite3.Row | None:
    if source_repo:
        return conn.execute(
            "SELECT dispatch_id, thread_id, resolved_model, status, started_at, "
            "last_heartbeat_at, record_json, packet_path, source_repo "
            "FROM cursor_sdk_dispatches "
            "WHERE source_repo=? AND COALESCE(read_only,0)=0 "
            "AND status IN ('admitted','running') LIMIT 1",
            (source_repo,),
        ).fetchone()
    return conn.execute(
        "SELECT dispatch_id, thread_id, resolved_model, status, started_at, "
        "last_heartbeat_at, record_json, packet_path, source_repo "
        "FROM cursor_sdk_dispatches "
        "WHERE COALESCE(read_only,0)=0 AND status IN ('admitted','running') LIMIT 1"
    ).fetchone()


def _response_from_row(
    row: sqlite3.Row,
    *,
    admission: CursorDispatchResponse,
    queue_position: int | None = None,
    holder: dict[str, Any] | None = None,
) -> CursorDispatchResponse:
    status = row["status"]
    if status == _STATUS_QUEUED:
        holder_fields = holder or _holder_projection(None)
        return CursorDispatchResponse(
            admitted=False,
            dispatch_id=row["dispatch_id"],
            thread_id=row["thread_id"],
            model_id=row["resolved_model"],
            status="queued",
            queue_position=queue_position,
            since=row["queued_at"],
            **holder_fields,
        )
    return CursorDispatchResponse(
        admitted=True,
        dispatch_id=row["dispatch_id"],
        thread_id=row["thread_id"],
        model_id=row["resolved_model"],
        status="admitted",
    )


def _migrate_queued_status(conn: sqlite3.Connection) -> None:
    """Extend the status CHECK to include ``queued`` on pre-v4 databases."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cursor_sdk_dispatches'"
    ).fetchone()
    if row is None or row["sql"] is None or "'queued'" in row["sql"]:
        return
    conn.executescript(
        """
        CREATE TABLE cursor_sdk_dispatches_v4 (
            dispatch_id        TEXT PRIMARY KEY,
            fingerprint        TEXT NOT NULL,
            thread_id          TEXT NOT NULL,
            execution_id       TEXT,
            caller_agent       TEXT,
            resolved_model     TEXT NOT NULL,
            packet_path        TEXT,
            message_present    INTEGER NOT NULL DEFAULT 0,
            state_root         TEXT,
            sdk_agent_id       TEXT,
            sdk_run_id         TEXT,
            status             TEXT NOT NULL CHECK (status IN ('queued','admitted','running','completed','failed')),
            started_at         TEXT,
            last_heartbeat_at  TEXT,
            terminal_status    TEXT CHECK (terminal_status IN ('completed','failed')),
            terminal_at        TEXT,
            record_json        TEXT NOT NULL DEFAULT '{}',
            wt_baseline        TEXT,
            contract           TEXT,
            source_repo        TEXT,
            read_only          INTEGER NOT NULL DEFAULT 0,
            worker_instance    TEXT,
            queued_at          TEXT
        );
        INSERT INTO cursor_sdk_dispatches_v4 (
            dispatch_id, fingerprint, thread_id, execution_id, caller_agent,
            resolved_model, packet_path, message_present, state_root,
            sdk_agent_id, sdk_run_id, status, started_at, last_heartbeat_at,
            terminal_status, terminal_at, record_json, wt_baseline, contract,
            source_repo, read_only, worker_instance, queued_at
        )
        SELECT
            dispatch_id, fingerprint, thread_id, execution_id, caller_agent,
            resolved_model, packet_path, message_present, state_root,
            sdk_agent_id, sdk_run_id, status, started_at, last_heartbeat_at,
            terminal_status, terminal_at, record_json, wt_baseline, contract,
            source_repo, read_only, worker_instance, NULL
        FROM cursor_sdk_dispatches;
        DROP TABLE cursor_sdk_dispatches;
        ALTER TABLE cursor_sdk_dispatches_v4 RENAME TO cursor_sdk_dispatches;
        CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_running
            ON cursor_sdk_dispatches(status) WHERE status = 'running';
        CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_execution
            ON cursor_sdk_dispatches(execution_id);
        """
    )


def _migrate_parked_waiting_status(conn: sqlite3.Connection) -> None:
    """Extend status CHECK with ``parked_waiting`` + park_child column."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='cursor_sdk_dispatches'"
    ).fetchone()
    if row is None or row["sql"] is None:
        return
    needs_status = "'parked_waiting'" not in row["sql"]
    cols = {
        r["name"] for r in conn.execute("PRAGMA table_info(cursor_sdk_dispatches)")
    }
    needs_col = "park_child_dispatch_id" not in cols
    if not needs_status and not needs_col:
        return
    if needs_col and not needs_status:
        conn.execute(
            "ALTER TABLE cursor_sdk_dispatches "
            "ADD COLUMN park_child_dispatch_id TEXT"
        )
        return
    has_source_ref = "source_ref" in cols
    source_ref_select = "source_ref" if has_source_ref else "NULL"
    conn.executescript(
        f"""
        CREATE TABLE cursor_sdk_dispatches_v5 (
            dispatch_id        TEXT PRIMARY KEY,
            fingerprint        TEXT NOT NULL,
            thread_id          TEXT NOT NULL,
            execution_id       TEXT,
            caller_agent       TEXT,
            resolved_model     TEXT NOT NULL,
            packet_path        TEXT,
            message_present    INTEGER NOT NULL DEFAULT 0,
            state_root         TEXT,
            sdk_agent_id       TEXT,
            sdk_run_id         TEXT,
            status             TEXT NOT NULL CHECK (
                status IN ({_STATUS_CHECK})
            ),
            started_at         TEXT,
            last_heartbeat_at  TEXT,
            terminal_status    TEXT CHECK (terminal_status IN ('completed','failed')),
            terminal_at        TEXT,
            record_json        TEXT NOT NULL DEFAULT '{{}}',
            wt_baseline        TEXT,
            contract           TEXT,
            source_repo        TEXT,
            read_only          INTEGER NOT NULL DEFAULT 0,
            worker_instance    TEXT,
            queued_at          TEXT,
            source_ref         TEXT,
            park_child_dispatch_id TEXT
        );
        INSERT INTO cursor_sdk_dispatches_v5 (
            dispatch_id, fingerprint, thread_id, execution_id, caller_agent,
            resolved_model, packet_path, message_present, state_root,
            sdk_agent_id, sdk_run_id, status, started_at, last_heartbeat_at,
            terminal_status, terminal_at, record_json, wt_baseline, contract,
            source_repo, read_only, worker_instance, queued_at, source_ref,
            park_child_dispatch_id
        )
        SELECT
            dispatch_id, fingerprint, thread_id, execution_id, caller_agent,
            resolved_model, packet_path, message_present, state_root,
            sdk_agent_id, sdk_run_id, status, started_at, last_heartbeat_at,
            terminal_status, terminal_at, record_json, wt_baseline, contract,
            source_repo, read_only, worker_instance, queued_at,
            {source_ref_select},
            NULL
        FROM cursor_sdk_dispatches;
        DROP TABLE cursor_sdk_dispatches;
        ALTER TABLE cursor_sdk_dispatches_v5 RENAME TO cursor_sdk_dispatches;
        CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_running
            ON cursor_sdk_dispatches(status) WHERE status = 'running';
        CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_execution
            ON cursor_sdk_dispatches(execution_id);
        CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_parked
            ON cursor_sdk_dispatches(source_repo, status)
            WHERE status = 'parked_waiting';
        """
    )


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
            if "wt_baseline" not in cols:
                conn.execute(
                    "ALTER TABLE cursor_sdk_dispatches ADD COLUMN wt_baseline TEXT"
                )
            if "contract" not in cols:
                conn.execute(
                    "ALTER TABLE cursor_sdk_dispatches ADD COLUMN contract TEXT"
                )
            if "source_repo" not in cols:
                conn.execute(
                    "ALTER TABLE cursor_sdk_dispatches ADD COLUMN source_repo TEXT"
                )
            if "read_only" not in cols:
                conn.execute(
                    "ALTER TABLE cursor_sdk_dispatches "
                    "ADD COLUMN read_only INTEGER NOT NULL DEFAULT 0"
                )
            if "worker_instance" not in cols:
                conn.execute(
                    "ALTER TABLE cursor_sdk_dispatches ADD COLUMN worker_instance TEXT"
                )
            if "queued_at" not in cols:
                conn.execute(
                    "ALTER TABLE cursor_sdk_dispatches ADD COLUMN queued_at TEXT"
                )
            if "source_ref" not in cols:
                conn.execute(
                    "ALTER TABLE cursor_sdk_dispatches ADD COLUMN source_ref TEXT"
                )
            _migrate_queued_status(conn)
            _migrate_parked_waiting_status(conn)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_queued "
                "ON cursor_sdk_dispatches(source_repo, worker_instance, status) "
                "WHERE status = 'queued'"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_source_ref_active "
                "ON cursor_sdk_dispatches(source_ref, status) "
                "WHERE status IN ('queued','admitted','running')"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sdk_dispatch_parked "
                "ON cursor_sdk_dispatches(source_repo, status) "
                "WHERE status = 'parked_waiting'"
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
            "read_only": req.read_only,
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
        wt_baseline: str | None = None,
        contract: str | None = None,
        source_repo: str | None = None,
        read_only: bool = False,
        worker_instance: str | None = None,
        source_ref: str | None = None,
        force: bool = False,
        nest_under: str | None = None,
    ) -> CursorDispatchResponse | None:
        """Durable idempotency (F2). Returns cached admission on hit, None on first
        admitted insert, or a queued ticket when the write-lease is held.

        Same-``source_ref`` implement gate (``contract=implement`` only): when
        ``source_ref`` is set and ``force`` is false, a peer row with the same
        ``source_ref`` and status in ``{queued,admitted,running}`` raises
        ``SourceRefConflict`` without inserting. The SELECT and INSERT share the
        existing ``BEGIN IMMEDIATE`` transaction, so concurrent admits cannot
        both observe an empty peer set and both insert.

        Nest park: when ``nest_under`` names the live write-lease holder for
        ``source_repo``, that parent is moved to ``parked_waiting`` and the
        child inserts as ``admitted`` (caller must also ``transfer_holder``).

        Raises ``DispatchConflict`` on fingerprint mismatch."""
        record_json = _dispatch_record_json(req)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT dispatch_id, fingerprint, status, thread_id, resolved_model, "
                "queued_at, source_repo FROM cursor_sdk_dispatches WHERE dispatch_id = ?",
                (req.dispatch_id,),
            ).fetchone()
            if existing is not None:
                if existing["fingerprint"] != fingerprint:
                    raise DispatchConflict(
                        f"dispatch_id {req.dispatch_id!r} already admitted with "
                        "different payload fingerprint"
                    )
                if existing["status"] == _STATUS_QUEUED:
                    pos = self._queue_position_conn(
                        conn,
                        dispatch_id=req.dispatch_id,
                        source_repo=existing["source_repo"],
                        worker_instance=worker_instance,
                    )
                    holder = _holder_projection(
                        _fetch_active_holder_conn(
                            conn, source_repo=existing["source_repo"]
                        )
                    )
                    return _response_from_row(
                        existing,
                        admission=admission,
                        queue_position=pos,
                        holder=holder,
                    )
                return _response_from_row(existing, admission=admission)
            if (
                contract == "implement"
                and source_ref
                and not force
            ):
                peer = conn.execute(
                    "SELECT dispatch_id, thread_id FROM cursor_sdk_dispatches "
                    "WHERE source_ref=? AND dispatch_id<>? "
                    "AND status IN ('queued','admitted','running') LIMIT 1",
                    (source_ref, req.dispatch_id),
                ).fetchone()
                if peer is not None:
                    raise SourceRefConflict(
                        source_ref=source_ref,
                        holder_dispatch_id=peer["dispatch_id"],
                        holder_thread_id=peer["thread_id"],
                    )
            insert_status = _STATUS_ADMITTED
            queued_at: str | None = None
            nested_park_parent: str | None = None
            if not read_only and source_repo:
                conflict = conn.execute(
                    "SELECT dispatch_id FROM cursor_sdk_dispatches "
                    "WHERE source_repo=? AND COALESCE(read_only,0)=0 "
                    "AND status IN ('admitted','running') AND dispatch_id<>? "
                    "LIMIT 1",
                    (source_repo, req.dispatch_id),
                ).fetchone()
                prior_queued = conn.execute(
                    "SELECT dispatch_id FROM cursor_sdk_dispatches "
                    "WHERE source_repo=? AND COALESCE(read_only,0)=0 "
                    "AND status='queued' AND dispatch_id<>? LIMIT 1",
                    (source_repo, req.dispatch_id),
                ).fetchone()
                if (
                    conflict is not None
                    and nest_under
                    and nest_under == conflict["dispatch_id"]
                ):
                    parked = conn.execute(
                        "UPDATE cursor_sdk_dispatches SET status=?, "
                        "park_child_dispatch_id=? "
                        "WHERE dispatch_id=? AND status IN ('admitted','running')",
                        (
                            _STATUS_PARKED_WAITING,
                            req.dispatch_id,
                            nest_under,
                        ),
                    )
                    if parked.rowcount == 1:
                        nested_park_parent = nest_under
                        insert_status = _STATUS_ADMITTED
                        queued_at = None
                    else:
                        insert_status = _STATUS_QUEUED
                        queued_at = _now()
                elif conflict is not None or prior_queued is not None:
                    insert_status = _STATUS_QUEUED
                    queued_at = _now()
            conn.execute(
                "INSERT INTO cursor_sdk_dispatches "
                "(dispatch_id, fingerprint, thread_id, execution_id, caller_agent, "
                " resolved_model, packet_path, message_present, status, record_json, "
                " wt_baseline, contract, source_repo, read_only, worker_instance, "
                " queued_at, source_ref) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    req.dispatch_id,
                    fingerprint,
                    req.thread_id,
                    execution_id,
                    caller_agent,
                    resolved_model,
                    req.packet_path,
                    1 if req.message else 0,
                    insert_status,
                    record_json,
                    wt_baseline,
                    contract,
                    source_repo,
                    1 if read_only else 0,
                    worker_instance,
                    queued_at,
                    source_ref,
                ),
            )
            if nested_park_parent is not None:
                # Capacity transfer is caller's duty (cursor_sdk_park); ledger done.
                return None
            if insert_status == _STATUS_QUEUED:
                pos = self._queue_position_conn(
                    conn,
                    dispatch_id=req.dispatch_id,
                    source_repo=source_repo,
                    worker_instance=worker_instance,
                )
                row = conn.execute(
                    "SELECT dispatch_id, fingerprint, status, thread_id, resolved_model, "
                    "queued_at, source_repo FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                    (req.dispatch_id,),
                ).fetchone()
                assert row is not None
                holder = _holder_projection(
                    _fetch_active_holder_conn(conn, source_repo=source_repo)
                )
                return _response_from_row(
                    row, admission=admission, queue_position=pos, holder=holder
                )
        return None

    def park_for_nested(self, *, parent_id: str, child_id: str) -> bool:
        """Move parent ``admitted|running`` → ``parked_waiting`` for ``child_id``.

        Returns True when the parent row was parked.
        """
        with self._connect() as conn:
            updated = conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=? "
                "WHERE dispatch_id=? AND status IN ('admitted','running')",
                (_STATUS_PARKED_WAITING, child_id, parent_id),
            )
            return updated.rowcount == 1

    def restore_from_park(self, *, parent_id: str) -> str | None:
        """Restore parent ``parked_waiting`` → ``running``; return source_repo."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_repo FROM cursor_sdk_dispatches "
                "WHERE dispatch_id=? AND status=?",
                (parent_id, _STATUS_PARKED_WAITING),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, park_child_dispatch_id=NULL "
                "WHERE dispatch_id=? AND status=?",
                (_STATUS_RUNNING, parent_id, _STATUS_PARKED_WAITING),
            )
            return row["source_repo"]

    def find_parked_parent_for_child(
        self, *, child_id: str
    ) -> tuple[str, str | None] | None:
        """Return ``(parent_id, source_repo)`` when a parked parent points at child."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT dispatch_id, source_repo FROM cursor_sdk_dispatches "
                "WHERE status=? AND park_child_dispatch_id=? LIMIT 1",
                (_STATUS_PARKED_WAITING, child_id),
            ).fetchone()
        if row is None:
            return None
        return row["dispatch_id"], row["source_repo"]

    def has_parked_parent(self, *, source_repo: str) -> bool:
        """True when any ``parked_waiting`` row exists for ``source_repo``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM cursor_sdk_dispatches "
                "WHERE source_repo=? AND COALESCE(read_only,0)=0 "
                "AND status=? LIMIT 1",
                (source_repo, _STATUS_PARKED_WAITING),
            ).fetchone()
        return row is not None

    @staticmethod
    def _queue_position_conn(
        conn: sqlite3.Connection,
        *,
        dispatch_id: str,
        source_repo: str | None,
        worker_instance: str | None,
    ) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches "
            "WHERE source_repo=? AND COALESCE(read_only,0)=0 AND status='queued' "
            "AND rowid <= ("
            "  SELECT rowid FROM cursor_sdk_dispatches WHERE dispatch_id=?"
            ")",
            (source_repo, dispatch_id),
        ).fetchone()
        return int(row["n"]) if row is not None else 1

    def read_wt_baseline(self, *, dispatch_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT wt_baseline FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
        if row is None or not row["wt_baseline"]:
            return None
        try:
            parsed = json.loads(row["wt_baseline"])
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def set_wt_baseline(self, *, dispatch_id: str, wt_baseline: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET wt_baseline=? WHERE dispatch_id=?",
                (wt_baseline, dispatch_id),
            )

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

    def mark_terminal(self, *, dispatch_id: str, terminal_status: str) -> str | None:
        """Mark terminal; return ``source_repo`` when present for promotion."""
        assert terminal_status in _STATUS_TERMINAL
        source_repo: str | None = None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_repo FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            if row is not None:
                source_repo = row["source_repo"]
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, terminal_status=?, terminal_at=? "
                "WHERE dispatch_id=?",
                (terminal_status, terminal_status, _now(), dispatch_id),
            )
        return source_repo

    def promote_next_queued(
        self, *, source_repo: str, worker_instance: str | None
    ) -> PromotedDispatch | None:
        """Advance the FIFO head ``queued`` row to ``admitted`` when lease is free.

        Queued rows from a prior worker restart remain in the durable ledger;
        promotion is repo-global (not scoped to ``worker_instance``) and
        re-homes the head row onto the live worker.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parked = conn.execute(
                "SELECT dispatch_id FROM cursor_sdk_dispatches "
                "WHERE source_repo=? AND COALESCE(read_only,0)=0 "
                "AND status=? LIMIT 1",
                (source_repo, _STATUS_PARKED_WAITING),
            ).fetchone()
            if parked is not None:
                return None
            active = conn.execute(
                "SELECT dispatch_id FROM cursor_sdk_dispatches "
                "WHERE source_repo=? AND COALESCE(read_only,0)=0 "
                "AND status IN ('admitted','running') LIMIT 1",
                (source_repo,),
            ).fetchone()
            if active is not None:
                return None
            head = conn.execute(
                "SELECT dispatch_id, thread_id, execution_id, caller_agent, "
                "resolved_model, source_repo, contract, read_only, record_json "
                "FROM cursor_sdk_dispatches "
                "WHERE source_repo=? AND COALESCE(read_only,0)=0 AND status='queued' "
                "ORDER BY rowid ASC LIMIT 1",
                (source_repo,),
            ).fetchone()
            if head is None:
                return None
            updated = conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, queued_at=NULL, "
                "worker_instance=? "
                "WHERE dispatch_id=? AND status='queued'",
                (_STATUS_ADMITTED, worker_instance, head["dispatch_id"]),
            )
            if updated.rowcount != 1:
                return None
        return PromotedDispatch(
            dispatch_id=head["dispatch_id"],
            thread_id=head["thread_id"],
            execution_id=head["execution_id"],
            caller_agent=head["caller_agent"],
            resolved_model=head["resolved_model"],
            source_repo=head["source_repo"],
            contract=head["contract"],
            read_only=bool(head["read_only"]),
            record_json=head["record_json"] or "{}",
        )

    def stale_writers(
        self,
        *,
        threshold_s: float,
        dead_run_grace_s: float,
        worker_instance: str | None,
    ) -> list[str]:
        """``admitted``/``running`` rows on this instance past heartbeat staleness.

        Live asyncio tasks use ``threshold_s`` (long lease timeout). Rows whose task
        is missing or already done use ``dead_run_grace_s`` so finalize orphans reap
        quickly without waiting for the full lease horizon.
        """
        from datetime import datetime

        now = datetime.now(UTC).timestamp()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dispatch_id, last_heartbeat_at, started_at, status "
                "FROM cursor_sdk_dispatches "
                "WHERE worker_instance=? AND COALESCE(read_only,0)=0 "
                "AND status IN ('admitted','running')",
                (worker_instance,),
            ).fetchall()
        stale: list[str] = []
        for row in rows:
            task = self._tasks.get(row["dispatch_id"])
            task_live = task is not None and not task.done()
            grace_s = threshold_s if task_live else dead_run_grace_s
            cutoff = now - grace_s
            ts = row["last_heartbeat_at"] or row["started_at"]
            if ts is None:
                stale.append(row["dispatch_id"])
                continue
            try:
                seen = datetime.fromisoformat(ts).timestamp()
            except ValueError:
                stale.append(row["dispatch_id"])
                continue
            if seen < cutoff:
                stale.append(row["dispatch_id"])
        return stale

    def release_stale_writer(self, *, dispatch_id: str) -> str | None:
        """Conservatively fail a stale lease holder; return ``source_repo``."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_repo, status FROM cursor_sdk_dispatches WHERE dispatch_id=?",
                (dispatch_id,),
            ).fetchone()
            if row is None or row["status"] not in _ACTIVE_WRITER_STATUSES:
                return None
            if (
                self._tasks.get(dispatch_id) is not None
                and not self._tasks[dispatch_id].done()
            ):
                return None
            conn.execute(
                "UPDATE cursor_sdk_dispatches SET status=?, terminal_status=?, terminal_at=? "
                "WHERE dispatch_id=? AND status IN ('admitted','running')",
                ("failed", "failed", _now(), dispatch_id),
            )
            return row["source_repo"]

    def lease_snapshot(self, *, source_repo: str | None = None) -> dict[str, Any]:
        """Active write-lease holder + queued depth (F-3)."""
        with self._connect() as conn:
            if source_repo:
                holder = _fetch_active_holder_conn(conn, source_repo=source_repo)
                queued = conn.execute(
                    "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches "
                    "WHERE source_repo=? AND COALESCE(read_only,0)=0 AND status='queued'",
                    (source_repo,),
                ).fetchone()
            else:
                holder = _fetch_active_holder_conn(conn, source_repo=None)
                queued = conn.execute(
                    "SELECT COUNT(*) AS n FROM cursor_sdk_dispatches "
                    "WHERE COALESCE(read_only,0)=0 AND status='queued'"
                ).fetchone()
        projection = _holder_projection(holder)
        return {
            **projection,
            "holder_source_repo": (
                holder["source_repo"]
                if holder is not None
                else source_repo
            ),
            "queue_depth": int(queued["n"]) if queued else 0,
        }

    def dispatch_status_by_thread(self, *, thread_id: str) -> dict[str, Any] | None:
        """Latest cursor_sdk_dispatches row for a thread → status projection, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT dispatch_id, status, execution_id, started_at, "
                "last_heartbeat_at, COALESCE(read_only,0) AS read_only, queued_at "
                "FROM cursor_sdk_dispatches WHERE thread_id=? "
                "ORDER BY COALESCE(started_at, queued_at) DESC, rowid DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "dispatch_id": row["dispatch_id"],
            "status": row["status"],
            "execution_id": row["execution_id"],
            "started_at": row["started_at"],
            "last_heartbeat_at": row["last_heartbeat_at"],
            "read_only": bool(row["read_only"]),
            "queued_at": row["queued_at"],
        }

    def startup_reconcile(self, *, worker_instance: str) -> list[str]:
        """Mark restart survivors terminal; return repos needing promotion."""
        repos: set[str] = set()
        with self._connect() as conn:
            survivors = conn.execute(
                "SELECT dispatch_id, source_repo, status, worker_instance "
                "FROM cursor_sdk_dispatches "
                "WHERE status IN ('admitted','running') "
                "AND COALESCE(read_only,0)=0"
            ).fetchall()
        for row in survivors:
            dispatch_id = row["dispatch_id"]
            live = (
                self._tasks.get(dispatch_id) is not None
                and not self._tasks[dispatch_id].done()
            )
            if live:
                continue
            if (
                row["worker_instance"] != worker_instance
                or row["status"] == _STATUS_RUNNING
            ):
                self.mark_terminal(dispatch_id=dispatch_id, terminal_status="failed")
                if row["source_repo"]:
                    repos.add(row["source_repo"])
            elif (
                row["status"] == _STATUS_ADMITTED
                and row["worker_instance"] == worker_instance
            ):
                self.mark_terminal(dispatch_id=dispatch_id, terminal_status="failed")
                if row["source_repo"]:
                    repos.add(row["source_repo"])
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT DISTINCT source_repo FROM cursor_sdk_dispatches "
                "WHERE status='queued' AND COALESCE(read_only,0)=0 "
                "AND source_repo IS NOT NULL"
            ):
                repos.add(row["source_repo"])
        return sorted(repos)

    def load_promoted_request(
        self, promoted: PromotedDispatch
    ) -> CursorDispatchRequest:
        data = json.loads(promoted.record_json)
        return CursorDispatchRequest(
            thread_id=promoted.thread_id,
            model=str(data.get("model") or promoted.resolved_model),
            dispatch_id=promoted.dispatch_id,
            execution_id=promoted.execution_id or promoted.dispatch_id,
            caller_agent=promoted.caller_agent,
            packet_path=data.get("packet_path"),
            message=data.get("message"),
            handoff_contract=data.get("handoff_contract") or promoted.contract,
            prompt_preamble=data.get("prompt_preamble"),
            model_knobs=data.get("model_knobs"),
            read_only=bool(data.get("read_only", promoted.read_only)),
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
        live = [op["op_id"] for op in self.live_dispatch_projections()]
        return {"running": len(live), "dispatch_ids": live}

    def live_dispatch_projections(self) -> list[dict[str, Any]]:
        """Human-readable live cursor-sdk ops for busy / active-work probes.

        Same live-task filter as ``active_snapshot`` (orphans excluded), but
        projects subject/model/thread so operators are not stuck with opaque
        dispatch UUIDs when diagnosing a busy git-worker.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dispatch_id, thread_id, resolved_model, status, started_at, "
                "last_heartbeat_at, record_json, packet_path "
                "FROM cursor_sdk_dispatches WHERE status='running'"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            dispatch_id = row["dispatch_id"]
            task = self._tasks.get(dispatch_id)
            if task is None or task.done():
                continue
            holder = _holder_projection(row)
            out.append(
                {
                    "kind": "cursor_sdk",
                    "op_id": dispatch_id,
                    "route": "/api/v1/cursor/dispatch",
                    "admitted_at": holder.get("holder_started_at"),
                    "state": str(row["status"] or "running"),
                    "resolved_model": holder.get("holder_resolved_model"),
                    "subject_preview": holder.get("holder_subject_preview"),
                    "thread_id": holder.get("holder_thread_id"),
                    "started_at": holder.get("holder_started_at"),
                }
            )
        return out
