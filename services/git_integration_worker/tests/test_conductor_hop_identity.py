"""H1 conductor identity helper — SQL contract column sole source."""

from __future__ import annotations

import sqlite3

import pytest

from services.git_integration_worker.cursor_sdk_conductor_identity import (
    is_conductor_dispatch_row,
)


def test_is_conductor_dispatch_row_dict_contract() -> None:
    assert is_conductor_dispatch_row({"contract": "conductor"}) is True
    assert is_conductor_dispatch_row({"contract": "implement"}) is False
    assert is_conductor_dispatch_row({}) is False


def test_is_conductor_dispatch_row_sqlite_row() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (contract TEXT)")
    conn.execute("INSERT INTO t VALUES ('conductor')")
    row = conn.execute("SELECT contract FROM t").fetchone()
    assert row is not None
    assert is_conductor_dispatch_row(row) is True


def test_is_conductor_dispatch_row_ignores_record_json_packet_kind() -> None:
    """JSON packet_kind must not override SQL contract (H1 invariant)."""
    assert (
        is_conductor_dispatch_row(
            {"contract": "implement", "record_json": '{"packet_kind":"conductor"}'}
        )
        is False
    )


def test_holder_kind_from_row_uses_contract_only() -> None:
    from services.git_integration_worker.cursor_sdk_work_key_gate import (
        holder_kind_from_row,
    )

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (contract TEXT, packet_kind TEXT)")
    conn.execute("INSERT INTO t VALUES ('conductor', NULL)")
    row = conn.execute("SELECT * FROM t").fetchone()
    assert holder_kind_from_row(row) == "conductor"

    conn.execute("DELETE FROM t")
    conn.execute("INSERT INTO t VALUES ('implement', 'conductor')")
    row = conn.execute("SELECT * FROM t").fetchone()
    assert holder_kind_from_row(row) == "implement"
