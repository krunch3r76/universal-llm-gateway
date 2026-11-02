"""migration_025 admits route=remapped on endpoint_wake_ledger."""

from __future__ import annotations

import sqlite3

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.migrations import migration_025


def test_migration_025_admits_remapped_route(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    detail = create_thread(thread_id=None, slug="m025-remapped-route")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _, turn_number, _ = insert_turn(
        thread=thread,
        from_agent="cursor-sdk",
        to_agent="cursor",
        subject="CLOSEOUT|migration probe",
        body="probe",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, route) "
            "VALUES (?, ?, ?, ?, 'pending', 'exact')",
            ("cursor", thread, turn_id, turn_number),
        )
        conn.execute(
            "UPDATE endpoint_wake_ledger SET route='remapped' WHERE turn_id=?",
            (turn_id,),
        )
    with connect() as conn:
        row = conn.execute(
            "SELECT route FROM endpoint_wake_ledger WHERE turn_id=?", (turn_id,)
        ).fetchone()
    assert row is not None
    assert row["route"] == "remapped"


def test_migration_025_agent_id_route_still_valid(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    detail = create_thread(thread_id=None, slug="m025-agent-id-route")
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
    with connect() as conn:
        row = conn.execute(
            "SELECT route FROM endpoint_wake_ledger WHERE turn_id=?", (turn_id,)
        ).fetchone()
    assert row is not None
    assert row["route"] == "agent_id"


def test_migration_025_idempotent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    with connect() as conn:
        migration_025.run(conn)
        migration_025.run(conn)
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
        ).fetchone()
    assert ddl is not None
    assert "'remapped'" in str(ddl[0])


def test_migration_025_rejects_bogus_route(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    detail = create_thread(thread_id=None, slug="m025-bogus-route")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, _, turn_number, _ = insert_turn(
        thread=thread,
        from_agent="cursor-sdk",
        to_agent="cursor",
        subject="probe",
        body="probe",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, route) "
            "VALUES (?, ?, ?, ?, 'pending', 'exact')",
            ("cursor", thread, turn_id, turn_number),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE endpoint_wake_ledger SET route='bogus' WHERE turn_id=?",
                (turn_id,),
            )
