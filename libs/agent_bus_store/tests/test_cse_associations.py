"""Verification tests for append-only CSE session-address associations."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import init_db
from agent_bus_store.db.connection import connect
from agent_bus_store.db.cse_associations import associate_cse, get_current_cse
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient

_URL_A = "https://claude.ai/cowork/cse_01CodB7tom1281iY8BmZJcZM"
_URL_B = "https://claude.ai/cowork/cse_01RtRztEY8bgocgT1geurtRx"


@pytest.fixture()
def bus_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _create_lane(client: TestClient, slug: str) -> str:
    resp = client.post("/threads", json={"slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_thread_get_carries_cse_pair(bus_client: TestClient) -> None:
    thread_id = _create_lane(bus_client, "cse-get")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        bound = associate_cse(
            thread_id=thread_id,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-a",
            bound_by="web-anthropic",
        )
    assert bound is not None
    assert bound["cse_chat_url"] == _URL_A

    detail = bus_client.get(f"/threads/{thread_id}").json()
    assert detail["cse_chat_url"] == _URL_A
    assert detail["cse_registration_id"] == "reg-a"

    unbound = _create_lane(bus_client, "cse-unbound")
    empty = bus_client.get(f"/threads/{unbound}").json()
    assert empty["cse_chat_url"] is None
    assert empty["cse_registration_id"] is None


def test_fold_newest_row(bus_client: TestClient) -> None:
    thread_id = _create_lane(bus_client, "cse-fold")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        first = associate_cse(
            thread_id=thread_id,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-a",
        )
        second = associate_cse(
            thread_id=thread_id,
            cse_chat_url=_URL_B,
            cse_registration_id="reg-b",
        )
    assert first is not None and second is not None
    assert second["id"] > first["id"]

    current = get_current_cse(thread_id=thread_id)
    assert current["state"] == "associated"
    assert current["cse_chat_url"] == _URL_B
    assert current["cse_registration_id"] == "reg-b"

    with connect() as conn:
        rows = conn.execute(
            "SELECT id, cse_chat_url FROM thread_cse_associations "
            "WHERE thread_id = ? ORDER BY id",
            (thread_id,),
        ).fetchall()
    assert len(rows) == 2
    assert rows[0]["cse_chat_url"] == _URL_A
    assert rows[1]["cse_chat_url"] == _URL_B


def test_same_url_new_registration_appends(bus_client: TestClient) -> None:
    thread_id = _create_lane(bus_client, "cse-host-recycle")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        first = associate_cse(
            thread_id=thread_id,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-old",
        )
        second = associate_cse(
            thread_id=thread_id,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-new",
        )
    assert first is not None and second is not None
    current = get_current_cse(thread_id=thread_id)
    assert current["cse_chat_url"] == _URL_A
    assert current["cse_registration_id"] == "reg-new"


def test_identical_pair_is_noop(bus_client: TestClient) -> None:
    thread_id = _create_lane(bus_client, "cse-noop")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound") as emit:
        first = associate_cse(
            thread_id=thread_id,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-a",
        )
        again = associate_cse(
            thread_id=thread_id,
            cse_chat_url=_URL_A + "/",
            cse_registration_id="reg-a",
        )
    assert first is not None
    assert again is None
    assert emit.call_count == 1
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM thread_cse_associations WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()["n"]
    assert count == 1


def test_registration_only_refused(bus_client: TestClient) -> None:
    thread_id = _create_lane(bus_client, "cse-reg-only")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound") as emit:
        result = associate_cse(
            thread_id=thread_id,
            cse_chat_url=None,
            cse_registration_id="reg-only",
        )
    assert result is None
    emit.assert_not_called()
    current = get_current_cse(thread_id=thread_id)
    assert current["state"] == "none"


def test_non_cowork_url_refused(bus_client: TestClient) -> None:
    thread_id = _create_lane(bus_client, "cse-bad-url")
    result = associate_cse(
        thread_id=thread_id,
        cse_chat_url="https://claude.ai/chat/not-a-cse",
        cse_registration_id="reg-x",
    )
    assert result is None


def test_list_threads_merges_cse(bus_client: TestClient) -> None:
    thread_id = _create_lane(bus_client, "cse-list")
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_cse(
            thread_id=thread_id,
            cse_chat_url=_URL_A,
            cse_registration_id="reg-a",
        )
    listed = bus_client.get("/threads").json()["threads"]
    match = next(row for row in listed if row["id"] == thread_id)
    assert match["cse_chat_url"] == _URL_A
    assert match["cse_registration_id"] == "reg-a"
