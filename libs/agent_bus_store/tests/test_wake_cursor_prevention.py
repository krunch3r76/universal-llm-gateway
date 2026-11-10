"""Trigger, pytest isolation, rewind helper, and endpoint_wake telemetry guards."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect, write_connect
from agent_bus_store.db.endpoint_wake_ledger import CONSUMER, advance_scan, get_cursor
from agent_bus_store.testing.wake_cursor_test_support import (
    refuse_live_agent_bus_db_under_pytest,
    rewind_wake_cursor_for_test,
    set_pytest_tmp_roots,
)


def test_migration_030_installs_no_rewind_trigger(tmp_path: Path) -> None:
    db_path = tmp_path / "m030.db"
    import os

    os.environ["AGENT_BUS_DB_PATH"] = str(db_path)
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
            ("endpoint_wake_cursor_no_rewind",),
        ).fetchone()
    assert row is not None


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


def test_rewind_helper_refuses_db_outside_pytest_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path.parent / "outside-wake.db"
    conn = sqlite3.connect(outside)
    conn.executescript(
        "CREATE TABLE endpoint_wake_cursor "
        "(consumer TEXT PRIMARY KEY, high_water_turn_id INTEGER, updated_at TEXT)"
    )
    set_pytest_tmp_roots(tmp_path / "allowed-only")
    with pytest.raises(RuntimeError, match="refuses DB outside pytest tmp"):
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    conn.close()


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

    home = Path(pwd.getpwuid(__import__("os").getuid()).pw_dir)
    live = home / ".agent-bus" / "messages.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(live))
    with pytest.raises(RuntimeError, match="refusing to open the live agent-bus DB"):
        with connect():
            pass


def test_pass_duration_emit_does_not_open_live_event_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_bus_store.testing.wake_cursor_test_support import (
        isolate_endpoint_wake_event_emit_for_pytest,
    )
    from bus_watch.endpoint_wake_pass_timing import (
        begin_pass,
        end_phase,
        reset_pass_timing_for_tests,
        safe_emit_pass_duration,
    )

    isolate_endpoint_wake_event_emit_for_pytest(monkeypatch, tmp_path)
    opens: list[str] = []

    try:
        from event_store.store import EventStore
    except ImportError:
        pytest.skip("event_store not importable")
        return

    original_init = EventStore.__init__

    def _track_init(self, db_path, *args, **kwargs):  # type: ignore[no-untyped-def]
        opens.append(str(db_path))
        return original_init(self, db_path, *args, **kwargs)

    with patch.object(EventStore, "__init__", _track_init):
        reset_pass_timing_for_tests()
        begin_pass()
        end_phase("scan_once")
        safe_emit_pass_duration()

    import pwd

    home = str(Path(pwd.getpwuid(__import__("os").getuid()).pw_dir))
    assert not any(p.startswith(home) and "events" in p for p in opens)
