"""quiet_sweep._load_lane includes read_at on LaneTurnView (friction a:43412)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.quiet_sweep import _load_lane, sweep_quiet_with_wip


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def test_load_lane_maps_read_at() -> None:
    """Breaks when SELECT omits read_at — harvest gate never sees mark_read."""
    turn_row = {
        "turn_number": 2,
        "from_agent": "cursor-sdk",
        "created_at": "2026-08-07T01:00:00Z",
        "subject": "done",
        "body": "status: complete",
        "read_at": "2026-08-07T01:00:01Z",
    }
    thread_row = {"bus_lifecycle_state": "active"}
    link_row = {
        "execution_id": "exec-1",
        "terminal_status": "completed",
        "terminal_at": "2026-08-07T01:00:00Z",
        "linked_at": "2026-08-07T00:50:00Z",
        "pipeline_id": "cursor-sdk-generate",
    }

    conn = MagicMock()
    conn.execute.side_effect = [
        MagicMock(fetchone=MagicMock(return_value=thread_row)),
        MagicMock(fetchall=MagicMock(return_value=[link_row])),
        MagicMock(fetchall=MagicMock(return_value=[turn_row])),
    ]
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False

    with patch("agent_bus_store.quiet_sweep.connect", return_value=cm):
        loaded = _load_lane("T-load")

    assert loaded is not None
    _lifecycle, _links, turns = loaded
    assert len(turns) == 1
    assert turns[0].read_at == "2026-08-07T01:00:01Z"


class _TurnSelectCounterConn:
    """Delegates to a sqlite connection; counts parent turns SELECTs."""

    def __init__(self, conn, parent_thread_id: str) -> None:
        self._conn = conn
        self._parent_thread_id = parent_thread_id
        self.parent_turn_selects = 0

    def execute(self, sql, parameters=(), /, **kwargs):
        if (
            "FROM turns WHERE thread" in sql
            and parameters
            and str(parameters[0]) == self._parent_thread_id
        ):
            self.parent_turn_selects += 1
        if kwargs:
            return self._conn.execute(sql, parameters, **kwargs)
        return self._conn.execute(sql, parameters)

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def test_sweep_shared_parent_turns_select_once(bus_db) -> None:
    """Breaks when per-pass lane cache is missing — O(children) parent turns loads."""
    seat = "web-anthropic"
    parent, *_ = create_thread_with_turn(
        slug="cache-parent",
        from_agent=seat,
        to_agent="cursor",
        subject="standing",
        body="parent lane",
        lifecycle_state="pending",
    )
    parent_id = parent["id"]
    old = (datetime.now(UTC) - timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    child_ids: list[str] = []
    for idx in range(3):
        child, *_ = create_thread_with_turn(
            slug=f"cache-child-{idx}",
            from_agent=seat,
            to_agent="cursor",
            subject="commission",
            body="child work",
            lifecycle_state="pending",
        )
        child_id = child["id"]
        child_ids.append(child_id)
        with connect() as conn:
            conn.execute(
                "INSERT INTO thread_lane_associations "
                "(thread_id, parent_thread_id, lane_role) VALUES (?, ?, 'sub_mission')",
                (child_id, parent_id),
            )
        admit_dispatch(
            thread_id=child_id,
            execution_id=f"exec-cache-child-{idx}",
            pipeline_id="cursor-sdk-generate",
            caller_agent=seat,
        )
        with connect() as conn:
            conn.execute("UPDATE turns SET created_at = ? WHERE thread = ?", (old, child_id))
            conn.execute(
                "UPDATE threads SET bus_lifecycle_state = 'active', updated_at = ? "
                "WHERE id = ?",
                (old, child_id),
            )

    parent_turn_selects = 0
    real_connect = connect

    @contextmanager
    def counting_connect():
        nonlocal parent_turn_selects
        with real_connect() as conn:
            wrapper = _TurnSelectCounterConn(conn, parent_id)
            yield wrapper
            parent_turn_selects += wrapper.parent_turn_selects

    with (
        patch("agent_bus_store.quiet_sweep.connect", counting_connect),
        patch("agent_bus_store.quiet_sweep.emit_quiet_with_wip_fired"),
    ):
        sweep_quiet_with_wip(threshold_s=60.0)

    assert parent_turn_selects == 1
    assert len(child_ids) == 3
