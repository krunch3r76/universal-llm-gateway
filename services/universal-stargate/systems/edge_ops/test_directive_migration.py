"""Schema migration for duplicate cancel directive rows."""

from __future__ import annotations

from pathlib import Path

from systems.edge_ops.store.schema import ensure_schema_sync, open_connection


def test_ensure_schema_collapses_duplicate_cancel_rows(tmp_path: Path) -> None:
    """Unique index creation must not fail when duplicates already exist."""
    path = tmp_path / "edge-ops.db"
    with open_connection(path) as connection:
        connection.execute(
            "CREATE TABLE edge_op_directives ("
            "op_id TEXT NOT NULL, seq INTEGER NOT NULL, type TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, at_epoch REAL NOT NULL, "
            "PRIMARY KEY (op_id, seq))"
        )
        connection.executemany(
            "INSERT INTO edge_op_directives "
            "(op_id, seq, type, payload_json, at_epoch) VALUES (?, ?, ?, ?, ?)",
            (
                ("legacy-op", 1, "cancel", "{}", 1.0),
                ("legacy-op", 2, "cancel", "{}", 2.0),
                ("legacy-op", 3, "cancel", "{}", 3.0),
            ),
        )
        connection.commit()

    with open_connection(path) as connection:
        ensure_schema_sync(connection)

    with open_connection(path) as connection:
        rows = connection.execute(
            "SELECT seq, type FROM edge_op_directives WHERE op_id = 'legacy-op'"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["type"] == "cancel"
    assert int(rows[0]["seq"]) == 1
