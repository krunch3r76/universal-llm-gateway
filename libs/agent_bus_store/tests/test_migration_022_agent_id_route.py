"""migration_022 admits route=agent_id on endpoint_wake_ledger."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.migrations import migration_022


def test_migration_022_admits_agent_id_route(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    detail = create_thread(thread_id=None, slug="m022-agent-id-route")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _, turn_number, _ = insert_turn(
        thread=thread,
        from_agent="cursor-sdk",
        to_agent="composer-impresario-4ed020f2",
        subject="CLOSEOUT|migration probe",
        body="probe",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, route) "
            "VALUES (?, ?, ?, ?, 'pending', 'agent_id')",
            ("composer-impresario-4ed020f2", thread, turn_id, turn_number),
        )
        conn.execute(
            "UPDATE endpoint_wake_ledger SET route='agent_id' WHERE turn_id=?",
            (turn_id,),
        )
    with connect() as conn:
        row = conn.execute(
            "SELECT route FROM endpoint_wake_ledger WHERE turn_id=?", (turn_id,)
        ).fetchone()
    assert row is not None
    assert row["route"] == "agent_id"


def test_migration_022_idempotent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    with connect() as conn:
        migration_022.run(conn)
        migration_022.run(conn)
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
        ).fetchone()
    assert ddl is not None
    assert "'agent_id'" in str(ddl[0])
