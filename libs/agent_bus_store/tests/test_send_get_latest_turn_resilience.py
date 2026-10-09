"""Send continue path proceeds when get_latest_turn fails (house route control)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    app = create_app(db_path=str(db_path))
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_send_continue_when_get_latest_turn_raises(bus_db: TestClient) -> None:
    row = create_thread(thread_id=None, slug="latest-turn-fail", tags=[])
    thread_id = row["id"]
    with patch(
        "agent_bus_store.routes.threads.send.get_latest_turn",
        side_effect=RuntimeError("simulated db read failure"),
    ):
        resp = bus_db.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web",
                "subject": "note",
                "body": "hello after prior_turn lookup failed",
            },
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["turn"]["thread"] == thread_id
