"""Non-terminal dispatch journal transitions (started fold + append log).

Terminal writes remain in ``dispatch_journal``; this module owns the forward
migration, ``started`` rows, and ``fetch_record`` for any status. Continuation
admit uses the same database: one claim row per stopped execution, and one
lineage row per root execution.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_TRANSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS dispatch_record_transitions (
    execution_id TEXT NOT NULL,
    status       TEXT NOT NULL,
    at           TEXT NOT NULL,
    at_epoch     REAL NOT NULL
);
"""

_NEW_TABLE_SQL = """
CREATE TABLE dispatch_records (
    execution_id TEXT PRIMARY KEY,
    pipeline TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started','completed','failed')),
    caller_agent TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    completed_at_epoch REAL,
    updated_at TEXT NOT NULL,
    updated_at_epoch REAL NOT NULL,
    record_json TEXT NOT NULL
);
"""

_INDEX_TRANSITIONS = """
CREATE INDEX IF NOT EXISTS idx_dispatch_record_transitions_execution_id
    ON dispatch_record_transitions(execution_id);
"""

_INDEX_UPDATED = """
CREATE INDEX IF NOT EXISTS idx_dispatch_records_updated_at_epoch
    ON dispatch_records(updated_at_epoch);
"""

_CLAIMS_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_continuation_claims (
    stop_execution_id TEXT PRIMARY KEY,
    successor_execution_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL
)
"""

_LINEAGE_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_lineage (
    root_id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    version TEXT NOT NULL,
    steps_sha256 TEXT NOT NULL,
    source_text TEXT NOT NULL,
    options_json TEXT NOT NULL
)
"""

_CANCELLED_ERROR_CODES = frozenset(
    {
        "pipeline_execution_cancelled",
        "cancelled",
    }
)


@dataclass(frozen=True, slots=True)
class ContinuationClaim:
    """Result of one insert-or-fail claim on ``stop_execution_id``."""

    won: bool
    successor_execution_id: str


@dataclass(frozen=True, slots=True)
class ContinuationDecision:
    """Admit or refuse a ``resume_of`` before a successor run is registered."""

    admitted: bool
    http_status: int
    code: str
    message: str
    successor_execution_id: str | None = None
    lineage_root: str | None = None
    continuation_seq: int = 0


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def migrate_schema_sync(connection: sqlite3.Connection) -> None:
    """Idempotent schema migration. Old terminal-only dispatch records become a fold table plus a transition log."""
    connection.execute(_TRANSITIONS_DDL)
    connection.execute(_INDEX_TRANSITIONS)
    connection.execute(_CLAIMS_DDL)
    connection.execute(_LINEAGE_DDL)
    if not _table_columns(connection, "dispatch_records"):
        connection.executescript(
            _NEW_TABLE_SQL
            + _INDEX_UPDATED
            + """
CREATE INDEX IF NOT EXISTS idx_dispatch_records_completed_at
    ON dispatch_records(completed_at_epoch);
"""
        )
        connection.commit()
        return
    cols = _table_columns(connection, "dispatch_records")
    if "updated_at" in cols:
        connection.execute(_INDEX_UPDATED)
        connection.commit()
        return
    connection.execute("ALTER TABLE dispatch_records RENAME TO dispatch_records_old")
    connection.executescript(_NEW_TABLE_SQL)
    connection.execute(
        """
        INSERT INTO dispatch_records(
            execution_id, pipeline, status, caller_agent,
            started_at, completed_at, completed_at_epoch,
            updated_at, updated_at_epoch, record_json
        )
        SELECT execution_id, pipeline, status, caller_agent,
            started_at, completed_at, completed_at_epoch,
            completed_at, completed_at_epoch, record_json
        FROM dispatch_records_old
        """
    )
    connection.execute("DROP TABLE dispatch_records_old")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_dispatch_records_completed_at
            ON dispatch_records(completed_at_epoch);
        """
    )
    connection.execute(_INDEX_UPDATED)
    connection.commit()
    logger.info("dispatch_records schema migrated for started transitions")


def _iso_epoch(iso_ts: str) -> float:
    return datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).timestamp()


def write_transition_sync(
    path: Path,
    *,
    execution_id: str,
    pipeline: str,
    status: str,
    caller_agent: str | None,
    started_at: str,
    completed_at: str | None,
    record_json: dict[str, Any],
) -> int:
    """Upsert fold row and append one transition line; return JSON byte size."""
    now_iso = datetime.now().astimezone().isoformat().replace("+00:00", "Z")
    now_epoch = time.time()
    at_iso = completed_at if status != "started" and completed_at else started_at
    at_epoch = _iso_epoch(at_iso)
    completed_epoch = _iso_epoch(completed_at) if completed_at is not None else None
    payload_json = json.dumps(record_json, separators=(",", ":"), ensure_ascii=True)
    terminal_statuses = frozenset({"completed", "failed"})
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL;")
        migrate_schema_sync(connection)
        existing = connection.execute(
            "SELECT status FROM dispatch_records WHERE execution_id = ?",
            (execution_id,),
        ).fetchone()
        if status == "started":
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO dispatch_records(
                        execution_id, pipeline, status, caller_agent,
                        started_at, completed_at, completed_at_epoch,
                        updated_at, updated_at_epoch, record_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        execution_id,
                        pipeline,
                        status,
                        caller_agent,
                        started_at,
                        completed_at,
                        completed_epoch,
                        now_iso,
                        now_epoch,
                        payload_json,
                    ),
                )
            elif str(existing[0]) not in terminal_statuses:
                connection.execute(
                    """
                    UPDATE dispatch_records SET
                        pipeline = ?, status = ?, caller_agent = ?,
                        started_at = ?, updated_at = ?, updated_at_epoch = ?,
                        record_json = ?
                    WHERE execution_id = ?
                    """,
                    (
                        pipeline,
                        status,
                        caller_agent,
                        started_at,
                        now_iso,
                        now_epoch,
                        payload_json,
                        execution_id,
                    ),
                )
        else:
            connection.execute(
                """
                INSERT OR REPLACE INTO dispatch_records(
                    execution_id, pipeline, status, caller_agent,
                    started_at, completed_at, completed_at_epoch,
                    updated_at, updated_at_epoch, record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    pipeline,
                    status,
                    caller_agent,
                    started_at,
                    completed_at,
                    completed_epoch,
                    now_iso,
                    now_epoch,
                    payload_json,
                ),
            )
        connection.execute(
            """
            INSERT INTO dispatch_record_transitions(
                execution_id, status, at, at_epoch
            ) VALUES (?, ?, ?, ?)
            """,
            (execution_id, status, at_iso, at_epoch),
        )
        connection.commit()
    return len(payload_json.encode("utf-8"))


def fetch_record_sync(
    path: Path,
    execution_id: str,
) -> tuple[dict[str, Any], str, float] | None:
    """Return ``(record_json, status, updated_at_epoch)`` for any status, or None if the id is missing."""
    with sqlite3.connect(path) as connection:
        migrate_schema_sync(connection)
        row = connection.execute(
            """
            SELECT record_json, status, updated_at_epoch
            FROM dispatch_records
            WHERE execution_id = ?
            """,
            (execution_id,),
        ).fetchone()
    if row is None:
        return None
    payload_json, status, updated_epoch = row
    return json.loads(payload_json), str(status), float(updated_epoch)


def sweep_orphan_started_sync(
    path: Path,
    *,
    process_started_at: float,
) -> list[tuple[str, str]]:
    """Mark ``started`` rows from a prior process generation as ``failed``.

    Compare-and-set per row: ``UPDATE … WHERE execution_id=? AND status='started'``.
    Returns ``(execution_id, pipeline)`` for each row updated.
    """
    now_iso = datetime.now().astimezone().isoformat().replace("+00:00", "Z")
    now_epoch = time.time()
    updated: list[tuple[str, str]] = []
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL;")
        migrate_schema_sync(connection)
        rows = connection.execute(
            """
            SELECT execution_id, pipeline, started_at, record_json
            FROM dispatch_records
            WHERE status = 'started'
            """,
        ).fetchall()
        for execution_id, pipeline, started_at, record_json in rows:
            if _iso_epoch(str(started_at)) >= process_started_at:
                continue
            body = json.loads(record_json)
            body["status"] = "failed"
            body["state"] = "failed"
            body["completed_at"] = now_iso
            body["as_of"] = now_iso
            body["error"] = {
                "code": "interrupted_by_restart",
                "message": "Dispatch interrupted by process restart",
                "data": {"resumable": True},
            }
            payload_json = json.dumps(body, separators=(",", ":"), ensure_ascii=True)
            cur = connection.execute(
                """
                UPDATE dispatch_records SET
                    status = 'failed',
                    completed_at = ?,
                    completed_at_epoch = ?,
                    updated_at = ?,
                    updated_at_epoch = ?,
                    record_json = ?
                WHERE execution_id = ? AND status = 'started'
                """,
                (
                    now_iso,
                    now_epoch,
                    now_iso,
                    now_epoch,
                    payload_json,
                    execution_id,
                ),
            )
            if cur.rowcount:
                updated.append((str(execution_id), str(pipeline)))
        connection.commit()
    return updated


def prune_started_sync(
    path: Path,
    retention_seconds: float,
) -> int:
    """Delete ``started`` rows whose ``updated_at_epoch`` is older than now minus *retention_seconds*."""
    cutoff = time.time() - retention_seconds
    with sqlite3.connect(path) as connection:
        migrate_schema_sync(connection)
        deleted = connection.execute(
            """
            DELETE FROM dispatch_records
            WHERE status = 'started' AND updated_at_epoch < ?
            """,
            (cutoff,),
        ).rowcount
        connection.commit()
    return max(0, deleted)


def steps_sha256_from_dump(steps: list[dict[str, Any]]) -> str:
    """Stable SHA-256 of a pipeline's YAML steps dump."""
    payload = json.dumps(
        steps,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    connection.execute("PRAGMA journal_mode=WAL;")
    migrate_schema_sync(connection)
    return connection


def claim_continuation_sync(
    path: Path,
    *,
    stop_execution_id: str,
    successor_execution_id: str,
    claimed_at: str,
) -> ContinuationClaim:
    """Insert-or-fail on ``pipeline_continuation_claims.stop_execution_id``.

    The claim is one statement:

    ``INSERT INTO pipeline_continuation_claims
    (stop_execution_id, successor_execution_id, claimed_at) VALUES (?, ?, ?)``

    A primary-key conflict is not a second write. The loser reads the row the
    winner inserted and returns that ``successor_execution_id``.
    """
    connection = _connect(path)
    try:
        connection.execute(
            """
            INSERT INTO pipeline_continuation_claims(
                stop_execution_id, successor_execution_id, claimed_at
            ) VALUES (?, ?, ?)
            """,
            (stop_execution_id, successor_execution_id, claimed_at),
        )
        connection.commit()
        return ContinuationClaim(
            won=True,
            successor_execution_id=successor_execution_id,
        )
    except sqlite3.IntegrityError:
        connection.rollback()
        row = connection.execute(
            """
            SELECT successor_execution_id
            FROM pipeline_continuation_claims
            WHERE stop_execution_id = ?
            """,
            (stop_execution_id,),
        ).fetchone()
        existing = (
            str(row[0])
            if row is not None and row[0] is not None
            else successor_execution_id
        )
        return ContinuationClaim(won=False, successor_execution_id=existing)
    finally:
        connection.close()


def read_lineage_sync(path: Path, root_id: str) -> dict[str, Any] | None:
    """Return the lineage row for ``root_id``, or None if it was never pinned."""
    connection = _connect(path)
    try:
        row = connection.execute(
            """
            SELECT root_id, pipeline_id, version, steps_sha256,
                   source_text, options_json
            FROM pipeline_lineage
            WHERE root_id = ?
            """,
            (root_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    return {
        "root_id": str(row[0]),
        "pipeline_id": str(row[1]),
        "version": str(row[2]),
        "steps_sha256": str(row[3]),
        "source_text": str(row[4]),
        "options_json": str(row[5]),
    }


def write_lineage_root_sync(
    path: Path,
    *,
    root_id: str,
    pipeline_id: str,
    version: str,
    steps_sha256: str,
    source_text: str,
    options_json: str,
) -> None:
    """Pin the root request. A second insert for the same root leaves the first pin."""
    connection = _connect(path)
    try:
        connection.execute(
            """
            INSERT INTO pipeline_lineage(
                root_id, pipeline_id, version, steps_sha256, source_text, options_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(root_id) DO NOTHING
            """,
            (root_id, pipeline_id, version, steps_sha256, source_text, options_json),
        )
        connection.commit()
    finally:
        connection.close()


def _predecessor_stop_sync(path: Path, successor_execution_id: str) -> str | None:
    connection = _connect(path)
    try:
        row = connection.execute(
            """
            SELECT stop_execution_id
            FROM pipeline_continuation_claims
            WHERE successor_execution_id = ?
            """,
            (successor_execution_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None or row[0] is None:
        return None
    return str(row[0])


def resolve_lineage_root_sync(path: Path, stop_execution_id: str) -> tuple[str, int]:
    """Walk claim predecessors until a lineage root.

    ``continuation_seq`` is 1 for a resume of the root.
    """
    current = stop_execution_id
    seq = 1
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        lineage = read_lineage_sync(path, current)
        if lineage is not None:
            return lineage["root_id"], seq
        predecessor = _predecessor_stop_sync(path, current)
        if predecessor is None:
            return current, seq
        current = predecessor
        seq += 1
    return stop_execution_id, 1


def _refusal_for_record(
    payload: dict[str, Any],
    status: str,
) -> tuple[str, str] | None:
    """Return ``(code, message)`` when ``resume_of`` must not start a successor."""
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    err_code = str(error.get("code") or "")
    if status == "cancelled" or err_code in _CANCELLED_ERROR_CODES:
        return ("cancelled", "A cancelled run cannot be resumed.")
    if status == "completed":
        result = payload.get("result")
        if not isinstance(result, dict):
            result = {}
        if result.get("stop"):
            return None
        return (
            "not_resumable",
            "Run completed without a stop and cannot be resumed.",
        )
    if status == "failed" and err_code == "interrupted_by_restart":
        return None
    return ("not_resumable", "Run is not a resumable stop.")


def assess_continuation_sync(
    path: Path,
    *,
    stop_execution_id: str,
    successor_execution_id: str,
    steps_sha256: str,
    pipeline_id: str,
    claimed_at: str,
) -> ContinuationDecision:
    """Refuse, or claim exactly one successor for ``stop_execution_id``."""
    if not stop_execution_id.strip() or not successor_execution_id.strip():
        return ContinuationDecision(
            admitted=False,
            http_status=422,
            code="not_resumable",
            message="resume_of and the successor execution id are required.",
        )
    fetched = fetch_record_sync(path, stop_execution_id)
    if fetched is None:
        return ContinuationDecision(
            admitted=False,
            http_status=422,
            code="not_resumable",
            message="No execution matches resume_of.",
        )
    payload, status, _updated = fetched
    refusal = _refusal_for_record(payload, status)
    if refusal is not None:
        code, message = refusal
        return ContinuationDecision(
            admitted=False,
            http_status=422,
            code=code,
            message=message,
        )
    lineage_root, continuation_seq = resolve_lineage_root_sync(path, stop_execution_id)
    lineage = read_lineage_sync(path, lineage_root)
    if lineage is None:
        return ContinuationDecision(
            admitted=False,
            http_status=422,
            code="not_resumable",
            message="Lineage root was never pinned.",
            lineage_root=lineage_root,
            continuation_seq=continuation_seq,
        )
    if lineage["pipeline_id"] != pipeline_id or lineage["steps_sha256"] != steps_sha256:
        return ContinuationDecision(
            admitted=False,
            http_status=422,
            code="lineage_spec_drift",
            message="Pipeline steps hash does not match the lineage root.",
            lineage_root=lineage_root,
            continuation_seq=continuation_seq,
        )
    claim = claim_continuation_sync(
        path,
        stop_execution_id=stop_execution_id,
        successor_execution_id=successor_execution_id,
        claimed_at=claimed_at,
    )
    if not claim.won:
        return ContinuationDecision(
            admitted=False,
            http_status=409,
            code="continuation_claimed",
            message="A continuation of this stop was already claimed.",
            successor_execution_id=claim.successor_execution_id,
            lineage_root=lineage_root,
            continuation_seq=continuation_seq,
        )
    return ContinuationDecision(
        admitted=True,
        http_status=202,
        code="admitted",
        message="Continuation claimed.",
        successor_execution_id=claim.successor_execution_id,
        lineage_root=lineage_root,
        continuation_seq=continuation_seq,
    )
