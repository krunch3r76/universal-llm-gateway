"""Concurrency checks for fold-time cancel directive writes."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path

from systems.edge_ops.store.schema import (
    ensure_schema_sync,
    fold_due_sync,
    open_connection,
)


def _seed_started_past_deadline(path: Path, *, now: float) -> None:
    with open_connection(path) as connection:
        ensure_schema_sync(connection)
        connection.execute(
            "INSERT INTO edge_ops (op_id, kind, op, args_json, redelivery, "
            "idempotency_key, state, admitted_at_epoch, deadline_epoch, "
            "dedupe_until_epoch, leased_by, lease_id, lease_expires_epoch, "
            "started_at_epoch, updated_at_epoch) "
            "VALUES ('conc-op', 'ping', 'ping', '{}', 'safe', 'conc', "
            "'started', ?, ?, ?, 'edge-a', 'lease-x', ?, ?, ?)",
            (now - 60.0, now - 1.0, now + 400.0, now + 30.0, now - 2.0, now),
        )
        connection.commit()


def _fold_once(path: Path, now: float) -> None:
    with open_connection(path) as connection:
        ensure_schema_sync(connection)
        fold_due_sync(connection, now)


def test_concurrent_folds_insert_one_cancel_directive(tmp_path: Path) -> None:
    """Two connections folding the same journal must not raise and write one cancel."""
    path = tmp_path / "edge-ops.db"
    now = 1_700_000_000.0
    _seed_started_past_deadline(path, now=now)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_fold_once, path, now) for _ in range(2)]
        for future in futures:
            future.result()

    with open_connection(path) as connection:
        rows = connection.execute(
            "SELECT seq, type FROM edge_op_directives WHERE op_id = 'conc-op'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["type"] == "cancel"
