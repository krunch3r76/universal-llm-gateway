"""Cursor-sdk dispatch thread auto-close parity with api-generate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.close_on_read import (
    CLOSE_ON_READ_TAG,
    append_close_on_read_marker,
)
from agent_bus_store.db import (
    admit_dispatch,
    create_thread_with_turn,
    get_thread,
    init_db,
)
from agent_bus_store.db.connection import connect
from agent_bus_store.db.threads import set_thread_tags
from agent_bus_store.disposition import append_bus_lifecycle_tags
from agent_bus_store.turns_models import ThreadStatus


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _sdk_generate_tags(*, lifecycle: str) -> list[str]:
    tags = append_bus_lifecycle_tags(
        ["cursor-sdk-generate", "type:generate", "contract:implement"],
        bus_lifecycle=lifecycle,  # type: ignore[arg-type]
    )
    return append_close_on_read_marker(tags, bus_lifecycle=lifecycle)


def _post_closeout(bus_db: TestClient, thread_id: str) -> None:
    resp = bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch closeout",
            "body": "closeout summary",
            "after_turn": 1,
        },
    )
    assert resp.status_code == 201


def test_cursor_sdk_generate_tags_get_close_on_read() -> None:
    tags = append_bus_lifecycle_tags(
        ["cursor-sdk-generate", "type:generate", "contract:implement"],
        bus_lifecycle="persistent",
    )
    marked = append_close_on_read_marker(tags, bus_lifecycle="persistent")
    assert CLOSE_ON_READ_TAG in marked
    assert "bus_lifecycle:persistent" in marked


def test_ephemeral_default_closes_after_terminal(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-ephemeral",
        from_agent="dispatch",
        to_agent="cursor-sdk:dispatch:exec-1",
        subject="implement",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, _sdk_generate_tags(lifecycle="ephemeral"))
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-eph",
        pipeline_id="cursor-sdk-generate",
    )
    _post_closeout(bus_db, thread_id)

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-eph"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.CLOSED


def test_explicit_persistent_stays_active_after_terminal(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-persistent",
        from_agent="dispatch",
        to_agent="cursor-sdk:dispatch:exec-2",
        subject="implement",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, _sdk_generate_tags(lifecycle="persistent"))
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-persist",
        pipeline_id="cursor-sdk-generate",
    )
    _post_closeout(bus_db, thread_id)

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-persist"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.ACTIVE


def test_persistent_close_on_read_closes_after_mark_read_not_wait(bus_db) -> None:
    thread_row, *_ = create_thread_with_turn(
        slug="sdk-cor",
        from_agent="dispatch",
        to_agent="cursor-sdk:dispatch:exec-3",
        subject="implement",
        body="packet pointer",
        lifecycle_state="pending",
    )
    thread_id = thread_row["id"]
    with connect() as conn:
        set_thread_tags(conn, thread_id, _sdk_generate_tags(lifecycle="persistent"))
    admit_dispatch(
        thread_id=thread_id,
        execution_id="exec-cor",
        pipeline_id="cursor-sdk-generate",
    )
    closeout = bus_db.post(
        "/turns",
        json={
            "thread": thread_id,
            "from": "cursor-sdk",
            "to": "dispatch",
            "subject": "cursor-sdk dispatch closeout",
            "body": "closeout summary",
            "after_turn": 1,
        },
    )
    assert closeout.status_code == 201
    turn_id = closeout.json()["id"]

    bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed", "execution_id": "exec-cor"},
    )
    row = get_thread(thread_id)
    assert row is not None
    assert row["status"] == ThreadStatus.ACTIVE

    wait_resp = bus_db.get(
        f"/threads/{thread_id}/wait"
        "?after_turn=1&wait=0&completion=first_reply_from&from_agent=cursor-sdk"
    )
    assert wait_resp.status_code == 200
    assert wait_resp.json()["thread_status"] == ThreadStatus.ACTIVE

    mark_resp = bus_db.patch(f"/turns/{turn_id}/read")
    assert mark_resp.status_code == 200
    row = get_thread(thread_id)
    assert row is not None
    assert row["status"] == ThreadStatus.CLOSED


def test_spec_thread_never_auto_closed_on_dispatch_terminate(bus_db) -> None:
    """Caller-owned spec thread (no generate tags) stays active."""
    thread_row, *_ = create_thread_with_turn(
        slug="spec-thread",
        from_agent="dispatch",
        to_agent="dispatch",
        subject="todo arc context",
        body="spec prompt source",
    )
    thread_id = thread_row["id"]

    resp = bus_db.post(
        f"/threads/{thread_id}/dispatch-terminate",
        json={"terminal_status": "completed"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ThreadStatus.ACTIVE
