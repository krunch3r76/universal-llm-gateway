"""A reply posted after dispatch-terminate does not reopen the link."""

from __future__ import annotations

import pytest

from agent_bus_store.db import (
    admit_dispatch,
    create_thread,
    get_thread_with_links,
    init_db,
    terminate_dispatch,
)
from agent_bus_store.db.turns import insert_turn

pytestmark = pytest.mark.offline

_EXEC = "662daf5d-c192-46fa-bec3-066aa4284f1a"
_LANE = "12286"


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _rows(thread_id: str, execution_id: str) -> list[dict]:
    detail = get_thread_with_links(thread_id)
    assert detail is not None
    return [
        row for row in detail["dispatch_links"] if row["execution_id"] == execution_id
    ]


def test_late_reply_does_not_reopen_completed_link(bus_db) -> None:
    del bus_db
    create_thread(thread_id=_LANE, slug="lane-12286", lifecycle_state="active")
    admit_dispatch(
        thread_id=_LANE,
        execution_id=_EXEC,
        pipeline_id="cdp-generate",
        caller_agent="cursor-auto",
    )
    terminate_dispatch(
        thread_id=_LANE,
        terminal_status="completed",
        execution_id=_EXEC,
    )
    insert_turn(
        thread=_LANE,
        from_agent="web-anthropic",
        to_agent="cursor",
        subject="cdp reply — 662daf5d",
        body=f"execution_id: {_EXEC}\n",
    )
    rows = _rows(_LANE, _EXEC)
    assert len(rows) == 1
    assert rows[0]["terminal_status"] == "completed"
    assert all(row["terminal_status"] is not None for row in rows)

    again = terminate_dispatch(
        thread_id=_LANE,
        terminal_status="completed",
        execution_id=_EXEC,
    )
    assert again is not None
    rows = _rows(_LANE, _EXEC)
    assert len(rows) == 1
    assert rows[0]["terminal_status"] == "completed"
