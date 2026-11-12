"""DDL, connection handling and the due-transition fold for the edge-op store.

``$DATA_DIR/edge-ops.db`` is the authority for edge-op state. ``edge_ops``
holds the folded row; ``edge_op_transitions`` is the append log behind it;
``edge_op_events`` and ``edge_op_directives`` carry the open-typed messages in
each direction; ``edge_polls`` is the edge-presence record ``admit`` reads to
fail fast with ``edge_unavailable``.

There is no sweeper loop. ``fold_due_sync`` runs on every touch of the store
and decides expiry and lease loss from the stored timestamps, so a process
that was down for an hour folds exactly the same states on its next read as
one that stayed up. In-memory wake-ups (``waiter.py``) are hints, never
authority.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..model import (
    REASON_DEADLINE,
    REASON_LEASE_LOST_AFTER_STARTED,
    REASON_LEASE_LOST_BEFORE_STARTED,
)
from .directives_io import (
    collapse_duplicate_directives_sync,
    write_cancel_directive_sync,
)

_OPS_DDL = """
CREATE TABLE IF NOT EXISTS edge_ops (
    op_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    op TEXT NOT NULL,
    args_json TEXT NOT NULL,
    redelivery TEXT NOT NULL CHECK (redelivery IN ('safe', 'never')),
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'queued', 'leased', 'started',
            'completed', 'failed', 'expired', 'unknown'
        )
    ),
    admitted_at_epoch REAL NOT NULL,
    deadline_epoch REAL NOT NULL,
    dedupe_until_epoch REAL NOT NULL,
    leased_by TEXT,
    lease_id TEXT,
    lease_expires_epoch REAL,
    started_at_epoch REAL,
    result_json TEXT,
    updated_at_epoch REAL NOT NULL
);
"""

_TRANSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS edge_op_transitions (
    op_id TEXT NOT NULL,
    state TEXT NOT NULL,
    reason TEXT,
    at_epoch REAL NOT NULL
);
"""

_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS edge_op_events (
    op_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    at_epoch REAL NOT NULL,
    PRIMARY KEY (op_id, seq)
);
"""

_DIRECTIVES_DDL = """
CREATE TABLE IF NOT EXISTS edge_op_directives (
    op_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    at_epoch REAL NOT NULL,
    PRIMARY KEY (op_id, seq)
);
"""

_POLLS_DDL = """
CREATE TABLE IF NOT EXISTS edge_polls (
    edge_id TEXT PRIMARY KEY,
    last_poll_epoch REAL NOT NULL,
    kinds_json TEXT NOT NULL
);
"""

_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_edge_ops_dedupe "
    "ON edge_ops(idempotency_key, dedupe_until_epoch);",
    "CREATE INDEX IF NOT EXISTS idx_edge_ops_state "
    "ON edge_ops(state, admitted_at_epoch);",
    "CREATE INDEX IF NOT EXISTS idx_edge_op_transitions_op "
    "ON edge_op_transitions(op_id, at_epoch);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_edge_op_directives_op_type "
    "ON edge_op_directives(op_id, type);",
)

_ALL_DDL = (
    _OPS_DDL,
    _TRANSITIONS_DDL,
    _EVENTS_DDL,
    _DIRECTIVES_DDL,
    _POLLS_DDL,
)


def open_connection(path: Path) -> sqlite3.Connection:
    """Open the authority store in WAL mode with row access by column name."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA synchronous=NORMAL;")
    return connection


def ensure_schema_sync(connection: sqlite3.Connection) -> None:
    """Create every table and index if missing on an open connection.

    Idempotent, and cheap enough to run once per journal instance so a store
    reached without an explicit boot-time ``initialize`` still answers reads
    instead of raising ``no such table``.
    """
    for statement in _ALL_DDL:
        connection.execute(statement)
    collapse_duplicate_directives_sync(connection)
    for statement in _INDEX_DDL:
        connection.execute(statement)
    connection.commit()


def initialize_schema_sync(path: Path) -> None:
    """Create every table and index if missing. Idempotent."""
    with open_connection(path) as connection:
        ensure_schema_sync(connection)


def record_transition_sync(
    connection: sqlite3.Connection,
    *,
    op_id: str,
    state: str,
    reason: str | None,
    at_epoch: float,
) -> None:
    """Append one row to the transition log behind the folded op row."""
    connection.execute(
        "INSERT INTO edge_op_transitions (op_id, state, reason, at_epoch) "
        "VALUES (?, ?, ?, ?)",
        (op_id, state, reason, at_epoch),
    )


def fold_due_sync(
    connection: sqlite3.Connection,
    now: float,
) -> list[tuple[str, str, str, str]]:
    """Apply expiry and lease-loss rules, returning ``(op_id, kind, state, reason)``.

    Three rules, evaluated against stored timestamps only:

    - ``queued`` at or past its deadline becomes ``expired``; it never runs
      late.
    - ``leased`` with an elapsed lease returns to ``queued`` when its deadline
      is still ahead, and becomes ``expired`` when it is not. The handler had
      not acknowledged ``started``, so re-leasing cannot duplicate work a
      ``never`` kind already did.
    - ``started`` with an elapsed lease becomes ``unknown`` and never re-runs.

    The caller publishes one lifecycle event per returned row.
    """
    folded: list[tuple[str, str, str, str]] = []
    rows = connection.execute(
        "SELECT op_id, kind, state, deadline_epoch, lease_expires_epoch "
        "FROM edge_ops WHERE state IN ('queued', 'leased', 'started')"
    ).fetchall()
    for row in rows:
        if row["state"] == "started" and now >= row["deadline_epoch"]:
            if write_cancel_directive_sync(connection, op_id=row["op_id"], now=now):
                connection.commit()
        new_state, reason = _due_transition_for(row, now)
        if new_state is None or reason is None:
            continue
        applied = _apply_fold_sync(
            connection, row["op_id"], row["state"], new_state, reason, now
        )
        if applied:
            folded.append((row["op_id"], row["kind"], new_state, reason))
    if folded:
        connection.commit()
    return folded


def _due_transition_for(
    row: sqlite3.Row,
    now: float,
) -> tuple[str | None, str | None]:
    """Decide the fold for one non-terminal row, or ``(None, None)``."""
    state = row["state"]
    deadline_passed = now >= row["deadline_epoch"]
    lease_expires = row["lease_expires_epoch"]
    lease_lost = lease_expires is not None and now >= lease_expires

    if state == "queued":
        return ("expired", REASON_DEADLINE) if deadline_passed else (None, None)
    if state == "leased" and lease_lost:
        if deadline_passed:
            return "expired", REASON_DEADLINE
        return "queued", REASON_LEASE_LOST_BEFORE_STARTED
    if state == "started" and lease_lost:
        return "unknown", REASON_LEASE_LOST_AFTER_STARTED
    return None, None


def _apply_fold_sync(
    connection: sqlite3.Connection,
    op_id: str,
    expected_state: str,
    new_state: str,
    reason: str,
    now: float,
) -> bool:
    """Write the folded state when *expected_state* still holds. True when applied."""
    if new_state == "queued":
        updated = connection.execute(
            "UPDATE edge_ops SET state = 'queued', leased_by = NULL, "
            "lease_id = NULL, lease_expires_epoch = NULL, updated_at_epoch = ? "
            "WHERE op_id = ? AND state = ?",
            (now, op_id, expected_state),
        ).rowcount
    else:
        updated = connection.execute(
            "UPDATE edge_ops SET state = ?, updated_at_epoch = ? "
            "WHERE op_id = ? AND state = ?",
            (new_state, now, op_id, expected_state),
        ).rowcount
    if not updated:
        return False
    record_transition_sync(
        connection,
        op_id=op_id,
        state=new_state,
        reason=reason,
        at_epoch=now,
    )
    return True
