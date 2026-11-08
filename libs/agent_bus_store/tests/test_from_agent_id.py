"""item-5b AC1 — turns.from_agent_id persist + validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store.db import create_thread

TAB = "21060be7-f0a6-4fa1-9058-b117ce4fc526"


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from agent_bus_store.db import init_db

    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def test_post_turns_persists_from_agent_id(client: TestClient) -> None:
    detail = create_thread(thread_id=None, slug="fa-id-ok")
    assert detail is not None
    thread = str(detail["id"])
    resp = client.post(
        "/turns",
        json={
            "thread": thread,
            "from": "cursor",
            "to": "web-anthropic",
            "subject": "PING|x",
            "body": "x",
            "from_agent_id": TAB,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body.get("from_agent_id") == TAB
    assert body.get("warning") is None
    turn = client.get("/turns", params={"thread": thread, "last": 1}).json()["turns"][0]
    assert turn.get("from_agent_id") == TAB


def test_post_turns_invalid_from_agent_id_stores_null_with_warning(
    client: TestClient,
) -> None:
    detail = create_thread(thread_id=None, slug="fa-id-bad")
    assert detail is not None
    thread = str(detail["id"])
    resp = client.post(
        "/turns",
        json={
            "thread": thread,
            "from": "cursor",
            "to": "web-anthropic",
            "subject": "PING|y",
            "body": "y",
            "from_agent_id": "not-a-uuid",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body.get("from_agent_id") is None
    assert body.get("warning") == "from_agent_id_ignored"
