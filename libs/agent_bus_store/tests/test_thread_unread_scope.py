"""Recipient-scoped unread_count + unread_basis on thread_get / threads."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, get_turns, init_db, insert_turn
from agent_bus_store.db.threads_atomic import close_thread
from fastapi.testclient import TestClient


@pytest.fixture()
def bus_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _seed_mixed(thread_id: str) -> int:
    """Mixed recipients + one superseded row. Returns superseded turn_id."""
    insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent="web",
        subject="to-web",
        body="w",
    )
    insert_turn(
        thread=thread_id,
        from_agent="web",
        to_agent="cursor",
        subject="to-cursor",
        body="c",
    )
    insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent="all",
        subject="to-all",
        body="a",
    )
    insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent="team",
        subject="to-team",
        body="t",
    )
    old_id, _, _ = insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent="web",
        subject="old-web",
        body="old",
    )
    insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent="web",
        subject="new-web",
        body="new",
        supersedes_turn=old_id,
    )
    return old_id


def test_thread_get_carries_basis_envelope(bus_client: TestClient) -> None:
    thread = create_thread(thread_id=None, slug="unread-scope")
    assert thread is not None
    tid = thread["id"]
    insert_turn(
        thread=tid, from_agent="cursor", to_agent="web", subject="hi", body="b"
    )
    resp = bus_client.get(f"/threads/{tid}")
    after = datetime.now(UTC)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    basis = data["unread_basis"]
    assert basis["basis"] == "read_at_null"
    assert basis["recipient"] is None
    assert basis["includes_superseded"] is False
    assert basis["source"] == "agent_bus_store.threads"
    as_of = datetime.fromisoformat(basis["as_of"].replace("Z", "+00:00"))
    assert as_of <= after
    assert as_of.tzinfo is not None


def test_threads_list_carries_basis(bus_client: TestClient) -> None:
    create_thread(thread_id=None, slug="listed")
    resp = bus_client.get("/threads")
    assert resp.status_code == 200, resp.text
    row = resp.json()["threads"][0]
    assert row["unread_basis"]["basis"] == "read_at_null"
    assert row["unread_basis"]["includes_superseded"] is False


def test_recipient_scoped_parity_with_fetch_unread(bus_client: TestClient) -> None:
    thread = create_thread(thread_id=None, slug="mixed-inbox")
    assert thread is not None
    tid = thread["id"]
    _seed_mixed(tid)

    scoped = bus_client.get(f"/threads/{tid}", params={"to": "web"}).json()
    fetched = get_turns(thread=tid, to="web", unread=True)
    assert scoped["unread_count"] == len(fetched)
    assert scoped["unread_basis"]["recipient"] == "web"

    listed = bus_client.get("/threads", params={"to": "web"}).json()["threads"]
    match = next(r for r in listed if r["id"] == tid)
    assert match["unread_count"] == scoped["unread_count"]


def test_superseded_turns_excluded_from_thread_wide_count(
    bus_client: TestClient,
) -> None:
    thread = create_thread(thread_id=None, slug="supersede-pin")
    assert thread is not None
    tid = thread["id"]
    old_id = _seed_mixed(tid)
    data = bus_client.get(f"/threads/{tid}").json()
    assert data["unread_basis"]["includes_superseded"] is False
    all_unread = get_turns(thread=tid, unread=True, include_superseded=True)
    live_unread = get_turns(thread=tid, unread=True, include_superseded=False)
    assert any(row["id"] == old_id for row in all_unread)
    assert data["unread_count"] == len(live_unread)
    assert data["unread_count"] == len(all_unread) - 1


def test_close_still_yields_zero_unread(bus_client: TestClient) -> None:
    thread = create_thread(thread_id=None, slug="close-zero")
    assert thread is not None
    tid = thread["id"]
    _seed_mixed(tid)
    closed = close_thread(tid, mark_all_read=True)
    assert closed is not None
    data = bus_client.get(f"/threads/{tid}").json()
    assert data["unread_count"] == 0
    scoped = bus_client.get(f"/threads/{tid}", params={"to": "web"}).json()
    assert scoped["unread_count"] == 0
