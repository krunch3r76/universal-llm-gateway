"""Harvest marks and GET /threads/{id}/open-children."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.db.threads import set_thread_tags


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _tag_parent_lane_auto(parent_id: str) -> None:
    with connect() as conn:
        set_thread_tags(conn, parent_id, ["lane:cursor-auto"])


def _bind_child(client: TestClient, parent_id: str, child_id: str) -> None:
    resp = client.post(
        f"/threads/{child_id}/lane-bind",
        json={"parent_thread_id": parent_id, "lane_role": "sub_mission"},
    )
    assert resp.status_code == 200, resp.text


def _post_turn(
    client: TestClient,
    *,
    thread: str,
    from_agent: str,
    body: str,
    to: str = "cursor-auto",
) -> dict:
    resp = client.post(
        "/threads/send",
        json={
            "thread": thread,
            "from": from_agent,
            "to": to,
            "subject": "test",
            "body": body,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _turn_read_at(client: TestClient, thread_id: str, turn_number: int) -> str | None:
    resp = client.get(f"/turns?thread={thread_id}")
    assert resp.status_code == 200
    for row in resp.json()["turns"]:
        if row["turn_number"] == turn_number:
            return row.get("read_at")
    raise AssertionError(f"turn {turn_number} not found on {thread_id}")


def test_disposition_marks_closeout_not_sibling_unread(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="oc-parent-harvest")
    parent_id = parent["id"]
    child = create_thread(thread_id=None, slug="oc-child-harvest")
    child_id = child["id"]
    _tag_parent_lane_auto(parent_id)
    _bind_child(bus_db, parent_id, child_id)

    _post_turn(
        bus_db,
        thread=child_id,
        from_agent="cursor-sdk",
        body="TYPE: CONFER\nnotes\n",
    )
    closeout = _post_turn(
        bus_db,
        thread=child_id,
        from_agent="cursor-sdk",
        to="web-anthropic",
        body="TYPE: CLOSEOUT\nstatus: complete\n",
    )
    closeout_turn = closeout["turn"]["turn_number"]
    assert _turn_read_at(bus_db, child_id, closeout_turn) is None

    _post_turn(
        bus_db,
        thread=parent_id,
        from_agent="web-anthropic",
        body=f"TYPE: DISPOSITION\nchild: {child_id}\n",
    )
    assert _turn_read_at(bus_db, child_id, closeout_turn) is not None
    assert _turn_read_at(bus_db, child_id, 1) is None


def test_lane_prefix_same_as_child(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="oc-parent-lane")
    parent_id = parent["id"]
    child = create_thread(thread_id=None, slug="oc-child-lane")
    child_id = child["id"]
    _tag_parent_lane_auto(parent_id)
    _bind_child(bus_db, parent_id, child_id)

    closeout = _post_turn(
        bus_db,
        thread=child_id,
        from_agent="cursor-sdk",
        to="web-anthropic",
        body="TYPE: CLOSEOUT\nstatus: complete\n",
    )
    tn = closeout["turn"]["turn_number"]
    _post_turn(
        bus_db,
        thread=parent_id,
        from_agent="web-anthropic",
        body=f"TYPE: DISPOSITION\nlane: {child_id}\n",
    )
    assert _turn_read_at(bus_db, child_id, tn) is not None


def test_cursor_auto_disposition_does_not_mark(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="oc-parent-auto")
    parent_id = parent["id"]
    child = create_thread(thread_id=None, slug="oc-child-auto")
    child_id = child["id"]
    _tag_parent_lane_auto(parent_id)
    _bind_child(bus_db, parent_id, child_id)
    closeout = _post_turn(
        bus_db,
        thread=child_id,
        from_agent="cursor-sdk",
        to="web-anthropic",
        body="TYPE: CLOSEOUT\nstatus: complete\n",
    )
    tn = closeout["turn"]["turn_number"]
    _post_turn(
        bus_db,
        thread=parent_id,
        from_agent="cursor-auto",
        body=f"TYPE: DISPOSITION\nchild: {child_id}\n",
    )
    assert _turn_read_at(bus_db, child_id, tn) is None


def test_parent_without_lane_tag_does_not_mark(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="oc-parent-notag")
    parent_id = parent["id"]
    child = create_thread(thread_id=None, slug="oc-child-notag")
    child_id = child["id"]
    _bind_child(bus_db, parent_id, child_id)
    closeout = _post_turn(
        bus_db,
        thread=child_id,
        from_agent="cursor-sdk",
        to="web-anthropic",
        body="TYPE: CLOSEOUT\nstatus: complete\n",
    )
    tn = closeout["turn"]["turn_number"]
    _post_turn(
        bus_db,
        thread=parent_id,
        from_agent="web-anthropic",
        body=f"TYPE: DISPOSITION\nchild: {child_id}\n",
    )
    assert _turn_read_at(bus_db, child_id, tn) is None


def test_open_children_before_and_after_harvest(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="oc-parent-query")
    parent_id = parent["id"]
    child = create_thread(thread_id=None, slug="oc-child-query")
    child_id = child["id"]
    _tag_parent_lane_auto(parent_id)
    _bind_child(bus_db, parent_id, child_id)

    _post_turn(
        bus_db,
        thread=child_id,
        from_agent="cursor-sdk",
        to="web-anthropic",
        body="TYPE: CLOSEOUT\nstatus: complete\n",
    )

    before = bus_db.get(f"/threads/{parent_id}/open-children")
    assert before.status_code == 200, before.text
    assert child_id in before.json()["thread_ids"]

    _post_turn(
        bus_db,
        thread=parent_id,
        from_agent="web-anthropic",
        body=f"TYPE: DISPOSITION\nchild: {child_id}\n",
    )
    closed = bus_db.patch(f"/threads/{child_id}", json={"status": "closed"})
    assert closed.status_code == 200, closed.text

    after = bus_db.get(f"/threads/{parent_id}/open-children")
    assert after.status_code == 200, after.text
    assert child_id not in after.json()["thread_ids"]


def test_active_child_without_closeout_is_open(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="oc-parent-active")
    parent_id = parent["id"]
    child = create_thread(thread_id=None, slug="oc-child-active")
    child_id = child["id"]
    _tag_parent_lane_auto(parent_id)
    _bind_child(bus_db, parent_id, child_id)

    resp = bus_db.get(f"/threads/{parent_id}/open-children")
    assert resp.status_code == 200, resp.text
    assert child_id in resp.json()["thread_ids"]


def test_prose_mention_does_not_mark(bus_db: TestClient) -> None:
    parent = create_thread(thread_id=None, slug="oc-parent-prose")
    parent_id = parent["id"]
    child = create_thread(thread_id=None, slug="oc-child-prose")
    child_id = child["id"]
    _tag_parent_lane_auto(parent_id)
    _bind_child(bus_db, parent_id, child_id)
    closeout = _post_turn(
        bus_db,
        thread=child_id,
        from_agent="cursor-sdk",
        to="web-anthropic",
        body="TYPE: CLOSEOUT\nstatus: complete\n",
    )
    tn = closeout["turn"]["turn_number"]
    _post_turn(
        bus_db,
        thread=parent_id,
        from_agent="web-anthropic",
        body=f"TYPE: DISPOSITION\nsee thread {child_id}\n",
    )
    assert _turn_read_at(bus_db, child_id, tn) is None


def test_open_children_404_unknown_parent(bus_db: TestClient) -> None:
    resp = bus_db.get("/threads/999999/open-children")
    assert resp.status_code == 404
