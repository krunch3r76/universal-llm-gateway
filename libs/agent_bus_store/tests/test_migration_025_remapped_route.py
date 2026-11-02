"""migration_025 admits route=remapped on endpoint_wake_ledger."""

from __future__ import annotations

import sqlite3

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.migrations import migration_025

# Pre-025 ledger shape (migration_022 CHECK — no ``remapped``).
_LEDGER_PRE_025 = """\
CREATE TABLE endpoint_wake_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    turn_number INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending','claimed','delivered','refused','skipped','unknown')
    ),
    wake_id TEXT,
    route TEXT CHECK (route IN (
        'exact','wildcard','parent','window-binding','cse','agent_id'
    )),
    subscription_id INTEGER,
    cse_registration_id TEXT,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    claimed_at TEXT,
    finished_at TEXT,
    decided_by_rule TEXT,
    UNIQUE(agent, thread_id, turn_id)
)
"""


def _connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _revert_ledger_to_pre_025(db_path: str) -> None:
    raw = sqlite3.connect(db_path)
    raw.execute("PRAGMA foreign_keys=OFF")
    raw.execute("DROP TABLE endpoint_wake_ledger")
    raw.execute(_LEDGER_PRE_025)
    raw.execute("DELETE FROM schema_migrations WHERE id = 'migration_025'")
    raw.commit()
    raw.close()


def _prepare_pre_025_populated(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str, list[dict[str, object]]]:
    """022-shaped ledger with several rows; migration_025 not yet recorded."""
    db_path = str(tmp_path / "bus.db")
    monkeypatch.setenv("AGENT_BUS_DB_PATH", db_path)
    init_db()
    detail = create_thread(thread_id=None, slug="m025-data-preserve")
    assert detail is not None
    thread = str(detail["id"])

    seeds: list[dict[str, object]] = []
    route_specs = [
        ("agent-a", "exact", "pending", None),
        ("agent-b", "wildcard", "claimed", "rule-wild"),
        ("agent-c", "agent_id", "delivered", "rule-agent"),
        ("agent-d", "parent", "skipped", None),
    ]
    for agent, route, state, decided_by_rule in route_specs:
        turn_id, _, turn_number, _ = insert_turn(
            thread=thread,
            from_agent="cursor-sdk",
            to_agent=agent,
            subject=f"FACT|{agent}",
            body="seed",
        )
        seeds.append(
            {
                "agent": agent,
                "thread_id": thread,
                "turn_id": turn_id,
                "turn_number": turn_number,
                "state": state,
                "route": route,
                "decided_by_rule": decided_by_rule,
            }
        )

    _revert_ledger_to_pre_025(db_path)
    raw = sqlite3.connect(db_path)
    for row in seeds:
        raw.execute(
            "INSERT INTO endpoint_wake_ledger "
            "(agent, thread_id, turn_id, turn_number, state, route, decided_by_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row["agent"],
                row["thread_id"],
                row["turn_id"],
                row["turn_number"],
                row["state"],
                row["route"],
                row["decided_by_rule"],
            ),
        )
    raw.commit()
    raw.close()
    return db_path, thread, seeds


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


def test_migration_025_rebuild_preserves_populated_rows(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path, _thread, seeds = _prepare_pre_025_populated(tmp_path, monkeypatch)
    with _connect_db(db_path) as conn:
        ddl_before = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
        ).fetchone()
        assert ddl_before is not None
        assert "'remapped'" not in str(ddl_before[0])
        count_before = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger"
        ).fetchone()["n"]
        assert count_before == len(seeds)
        recorded = conn.execute(
            "SELECT id FROM schema_migrations WHERE id = 'migration_025'"
        ).fetchone()
        assert recorded is None

        migration_025.run(conn)
        conn.commit()

        master_after_first = conn.execute(
            "SELECT rootpage, sql FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
        ).fetchone()
        assert master_after_first is not None

        count_after_first = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger"
        ).fetchone()["n"]
        assert count_after_first == count_before

        rows = conn.execute(
            "SELECT agent, thread_id, turn_id, turn_number, state, route, "
            "decided_by_rule FROM endpoint_wake_ledger ORDER BY agent"
        ).fetchall()
        assert len(rows) == len(seeds)
        for row, seed in zip(rows, sorted(seeds, key=lambda s: str(s["agent"]))):
            assert row["agent"] == seed["agent"]
            assert row["thread_id"] == seed["thread_id"]
            assert row["turn_id"] == seed["turn_id"]
            assert row["turn_number"] == seed["turn_number"]
            assert row["state"] == seed["state"]
            assert row["route"] == seed["route"]
            assert row["decided_by_rule"] == seed["decided_by_rule"]

        remapped_turn = seeds[0]["turn_id"]
        conn.execute(
            "UPDATE endpoint_wake_ledger SET route='remapped' WHERE turn_id=?",
            (remapped_turn,),
        )
        conn.commit()
        remapped = conn.execute(
            "SELECT route FROM endpoint_wake_ledger WHERE turn_id=?",
            (remapped_turn,),
        ).fetchone()
        assert remapped is not None
        assert remapped["route"] == "remapped"

        migration_025.run(conn)
        conn.commit()

        count_after_second = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger"
        ).fetchone()["n"]
        assert count_after_second == count_before
        master_after_second = conn.execute(
            "SELECT rootpage, sql FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
        ).fetchone()
        assert master_after_second is not None
        assert master_after_second["rootpage"] == master_after_first["rootpage"]
        assert master_after_second["sql"] == master_after_first["sql"]
        assert "'remapped'" in str(master_after_second["sql"])


def test_migration_025_idempotent(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path, _thread, seeds = _prepare_pre_025_populated(tmp_path, monkeypatch)
    with _connect_db(db_path) as conn:
        count_before = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger"
        ).fetchone()["n"]
        assert count_before == len(seeds)
        migration_025.run(conn)
        conn.commit()
        master_after_first = conn.execute(
            "SELECT rootpage, sql FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
        ).fetchone()
        assert master_after_first is not None
        migration_025.run(conn)
        conn.commit()
        count_after = conn.execute(
            "SELECT COUNT(*) AS n FROM endpoint_wake_ledger"
        ).fetchone()["n"]
        assert count_after == count_before
        master_after_second = conn.execute(
            "SELECT rootpage, sql FROM sqlite_master WHERE type='table' AND name='endpoint_wake_ledger'"
        ).fetchone()
        assert master_after_second is not None
        assert master_after_second["rootpage"] == master_after_first["rootpage"]
        assert master_after_second["sql"] == master_after_first["sql"]
        assert "'remapped'" in str(master_after_second["sql"])


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
