"""Trigger, pytest isolation, rewind helper, and endpoint_wake telemetry guards."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_ledger import CONSUMER, advance_scan, get_cursor
from agent_bus_store.testing.wake_cursor_test_support import (
    isolate_endpoint_wake_event_emit_for_pytest,
    refuse_live_agent_bus_db_under_pytest,
    rewind_wake_cursor_for_test,
    set_pytest_tmp_roots,
    track_uds_put_nowait,
)


def test_migration_030_installs_no_rewind_trigger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "m030.db"))
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE ?",
            ("endpoint_wake_cursor_no_rewind%",),
        ).fetchall()
    names = {r[0] for r in rows}
    assert "endpoint_wake_cursor_no_rewind" in names
    assert "endpoint_wake_cursor_no_rewind_insert" in names


def test_backward_high_water_update_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    set_pytest_tmp_roots(tmp_path)
    init_db()
    detail = create_thread(thread_id=None, slug="trig")
    assert detail is not None
    turn_id, *_ = insert_turn(
        thread=str(detail["id"]),
        from_agent="a",
        to_agent="b",
        subject="FACT|x",
        body="b",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_cursor SET high_water_turn_id=? WHERE consumer=?",
            (turn_id, CONSUMER),
        )
    with connect() as conn:
        hw = conn.execute(
            "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
            (CONSUMER,),
        ).fetchone()[0]
    assert int(hw) == turn_id
    with pytest.raises(sqlite3.IntegrityError, match="backward high_water write refused"):
        with write_connect() as conn:
            conn.execute(
                "UPDATE endpoint_wake_cursor SET high_water_turn_id=? WHERE consumer=?",
                (0, CONSUMER),
            )
    with connect() as conn:
        hw_after = conn.execute(
            "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
            (CONSUMER,),
        ).fetchone()[0]
    assert int(hw_after) == turn_id


def test_equal_and_forward_high_water_updates_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    set_pytest_tmp_roots(tmp_path)
    init_db()
    detail = create_thread(thread_id=None, slug="fwd")
    assert detail is not None
    ids = []
    thread = str(detail["id"])
    for _ in range(3):
        tid, *_ = insert_turn(
            thread=thread, from_agent="a", to_agent="b", subject="FACT|x", body="b"
        )
        ids.append(tid)
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=ids[0])
    with write_connect() as conn:
        conn.execute(
            "UPDATE endpoint_wake_cursor SET high_water_turn_id=? WHERE consumer=?",
            (ids[0], CONSUMER),
        )
        conn.execute(
            "UPDATE endpoint_wake_cursor SET high_water_turn_id=? WHERE consumer=?",
            (ids[1], CONSUMER),
        )
    with connect() as conn:
        hw = conn.execute(
            "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
            (CONSUMER,),
        ).fetchone()[0]
    assert int(hw) == ids[1]


def test_advance_scan_still_advances(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    set_pytest_tmp_roots(tmp_path)
    init_db()
    detail = create_thread(thread_id=None, slug="adv")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, *_tn, num, _c = insert_turn(
        thread=thread, from_agent="a", to_agent="b", subject="FACT|x", body="b"
    )
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    item = {
        "agent": "b",
        "thread_id": thread,
        "turn_id": turn_id,
        "turn_number": num,
    }
    advance_scan(CONSUMER, 0, turn_id, [item])
    cursor = get_cursor()
    assert int(cursor["high_water_turn_id"]) == turn_id


def test_rewind_helper_allows_pytest_tmp_without_explicit_roots(tmp_path: Path) -> None:
    db_path = tmp_path / "rewrite.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE endpoint_wake_cursor "
        "(consumer TEXT PRIMARY KEY, high_water_turn_id INTEGER, updated_at TEXT)"
    )
    rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    hw = conn.execute(
        "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
        (CONSUMER,),
    ).fetchone()[0]
    assert int(hw) == 0
    conn.close()


def test_rewind_helper_refuses_home_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent_bus_store.testing.wake_cursor_test_support as support_mod

    home_io = Path("/home/io")
    outside = (
        home_io / "wake-rewind-guard-scratch.db"
        if home_io.is_dir()
        else Path("/var/tmp") / "agent-bus-rewind-guard-outside.db"
    )
    monkeypatch.setattr(
        support_mod, "_pytest_tmp_roots", [tmp_path / "only-this-subdir"]
    )
    conn = sqlite3.connect(outside)
    conn.executescript(
        "CREATE TABLE endpoint_wake_cursor "
        "(consumer TEXT PRIMARY KEY, high_water_turn_id INTEGER, updated_at TEXT)"
    )
    try:
        with pytest.raises(RuntimeError, match="refuses DB outside pytest tmp"):
            rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    finally:
        conn.close()
        outside.unlink(missing_ok=True)


def test_refuse_live_agent_bus_path_under_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "fake::test")
    import pwd

    home = Path(pwd.getpwuid(__import__("os").getuid()).pw_dir)
    live = home / ".agent-bus" / "messages.db"
    with pytest.raises(RuntimeError, match="refusing to open the live agent-bus DB"):
        refuse_live_agent_bus_db_under_pytest(live)


def test_connect_refuses_live_db_when_env_points_at_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pwd

    import agent_bus_store.db.connection as conn_mod

    def _sqlite_must_not_open(*_a: object, **_k: object) -> None:
        raise AssertionError("sqlite3.connect must not run when live guard tripped")

    monkeypatch.setattr(conn_mod.sqlite3, "connect", _sqlite_must_not_open)
    home = Path(pwd.getpwuid(__import__("os").getuid()).pw_dir)
    live = home / ".agent-bus" / "messages.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(live))
    with pytest.raises(RuntimeError, match="refusing to open the live agent-bus DB"):
        with connect():
            pass


def test_pass_duration_emit_isolated_under_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bus_watch.events as bw_events
    from agent_bus_store.testing.wake_cursor_test_support import (
        _import_mcp_events_module,
    )
    from bus_watch.endpoint_wake_pass_timing import (
        begin_pass,
        end_phase,
        reset_pass_timing_for_tests,
        safe_emit_pass_duration,
    )

    _import_mcp_events_module()
    uds_calls: list[tuple[str, dict[str, object]]] = []

    def _spy(signal: str, payload: dict[str, object]) -> None:
        uds_calls.append((signal, payload))

    bw_events.register_uds_publisher(_spy)
    enqueued = track_uds_put_nowait(monkeypatch)
    isolate_endpoint_wake_event_emit_for_pytest(monkeypatch, tmp_path)
    reset_pass_timing_for_tests()
    begin_pass()
    end_phase("scan_once")
    safe_emit_pass_duration()
    assert len(uds_calls) == 0
    assert len(enqueued) == 0


def test_pass_duration_emit_negative_control_records_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bus_watch.events as bw_events
    from agent_bus_store.testing.wake_cursor_test_support import (
        _import_mcp_events_module,
    )
    from bus_watch.endpoint_wake_pass_timing import (
        begin_pass,
        end_phase,
        reset_pass_timing_for_tests,
        safe_emit_pass_duration,
    )

    mcp_events = _import_mcp_events_module()
    uds_calls: list[tuple[str, dict[str, object]]] = []

    def _spy(signal: str, payload: dict[str, object]) -> None:
        uds_calls.append((signal, payload))

    bw_events.register_uds_publisher(_spy)
    enqueued = track_uds_put_nowait(monkeypatch)
    monkeypatch.setattr(bw_events, "record", mcp_events.record)
    mcp_events._publisher = mcp_events._UDSPublisher(
        "/nonexistent/negative-control.sock"
    )
    reset_pass_timing_for_tests()
    begin_pass()
    end_phase("scan_once")
    safe_emit_pass_duration()
    assert len(uds_calls) >= 1 or len(enqueued) >= 1


def test_insert_or_replace_lower_high_water_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    stamp = "2020-01-01T00:00:00Z"
    with connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_cursor (consumer, high_water_turn_id, updated_at) "
            "VALUES (?, ?, ?)",
            (CONSUMER, 100, stamp),
        )
    with pytest.raises(sqlite3.IntegrityError, match="backward high_water insert refused"):
        with connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO endpoint_wake_cursor "
                "(consumer, high_water_turn_id, updated_at) VALUES (?, ?, ?)",
                (CONSUMER, 50, stamp),
            )
    with connect() as conn:
        hw = conn.execute(
            "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
            (CONSUMER,),
        ).fetchone()[0]
    assert int(hw) == 100


def test_insert_or_replace_equal_and_forward_high_water_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    stamp = "2020-01-01T00:00:00Z"
    with connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_cursor (consumer, high_water_turn_id, updated_at) "
            "VALUES (?, ?, ?)",
            (CONSUMER, 100, stamp),
        )
        conn.execute(
            "INSERT OR REPLACE INTO endpoint_wake_cursor "
            "(consumer, high_water_turn_id, updated_at) VALUES (?, ?, ?)",
            (CONSUMER, 100, stamp),
        )
        conn.execute(
            "INSERT OR REPLACE INTO endpoint_wake_cursor "
            "(consumer, high_water_turn_id, updated_at) VALUES (?, ?, ?)",
            (CONSUMER, 200, stamp),
        )
    with connect() as conn:
        hw = conn.execute(
            "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
            (CONSUMER,),
        ).fetchone()[0]
    assert int(hw) == 200


def test_delete_then_insert_lower_high_water_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    stamp = "2020-01-01T00:00:00Z"
    with connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_cursor (consumer, high_water_turn_id, updated_at) "
            "VALUES (?, ?, ?)",
            (CONSUMER, 100, stamp),
        )
        rewind_wake_cursor_for_test(conn, CONSUMER, to=5)
        hw = conn.execute(
            "SELECT high_water_turn_id FROM endpoint_wake_cursor WHERE consumer=?",
            (CONSUMER,),
        ).fetchone()[0]
    assert int(hw) == 5


def test_get_cursor_after_newest_turn_deleted_keeps_stored_high_water(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    detail = create_thread(thread_id=None, slug="del-turn")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, *_tn, num, _c = insert_turn(
        thread=thread, from_agent="a", to_agent="b", subject="FACT|x", body="b"
    )
    item = {
        "agent": "b",
        "thread_id": thread,
        "turn_id": turn_id,
        "turn_number": num,
    }
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(CONSUMER, 0, turn_id, [item])
    from agent_bus_store.db.turns import delete_turn

    delete_turn(turn_id, force=True)
    cursor = get_cursor()
    assert int(cursor["high_water_turn_id"]) == turn_id


def test_get_cursor_route_ok_after_newest_turn_deleted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bus_store.auth import require_token
    from agent_bus_store.server import create_app
    from fastapi.testclient import TestClient

    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    detail = create_thread(thread_id=None, slug="del-route")
    assert detail is not None
    thread = str(detail["id"])
    turn_id, *_tn, num, _c = insert_turn(
        thread=thread, from_agent="a", to_agent="b", subject="FACT|x", body="b"
    )
    item = {
        "agent": "b",
        "thread_id": thread,
        "turn_id": turn_id,
        "turn_number": num,
    }
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    advance_scan(CONSUMER, 0, turn_id, [item])
    from agent_bus_store.db.turns import delete_turn

    delete_turn(turn_id, force=True)
    resp = client.get("/endpoint-wake/cursor")
    assert resp.status_code == 200
    assert int(resp.json()["high_water_turn_id"]) == turn_id


def test_get_cursor_keeps_stored_when_turns_table_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    stamp = "2020-01-01T00:00:00Z"
    with connect() as conn:
        conn.execute(
            "INSERT INTO endpoint_wake_cursor (consumer, high_water_turn_id, updated_at) "
            "VALUES (?, ?, ?)",
            (CONSUMER, 42, stamp),
        )
        conn.execute("DELETE FROM turns")
    cursor = get_cursor()
    assert int(cursor["high_water_turn_id"]) == 42


def test_get_cursor_cold_start_seeds_max_turn_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    detail = create_thread(thread_id=None, slug="cold")
    assert detail is not None
    turn_id, *_ = insert_turn(
        thread=str(detail["id"]),
        from_agent="a",
        to_agent="b",
        subject="FACT|x",
        body="b",
    )
    with connect() as conn:
        conn.execute("DELETE FROM endpoint_wake_cursor")
    cursor = get_cursor()
    assert int(cursor["high_water_turn_id"]) == turn_id
