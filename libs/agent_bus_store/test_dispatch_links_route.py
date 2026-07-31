"""HTTP tests for GET /dispatch-links/{execution_id}."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import admit_dispatch, create_thread_with_turn, init_db


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    return db_path


def _app(bus_db):
    app = create_app(db_path=str(bus_db))
    app.dependency_overrides[require_token] = lambda: None
    return app


def test_dispatch_link_lookup_hit(bus_db) -> None:
    with TestClient(_app(bus_db)) as client:
        thread_row, *_ = create_thread_with_turn(
            slug="sdk-link-lookup",
            from_agent="dispatch",
            to_agent="cursor-sdk:dispatch:exec-hit",
            subject="cursor-sdk generate",
            body="pointer",
            lifecycle_state="pending",
        )
        thread_id = thread_row["id"]
        admit_dispatch(
            thread_id=thread_id,
            execution_id="exec-hit",
            pipeline_id="cursor-sdk-generate",
        )

        resp = client.get("/dispatch-links/exec-hit")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["thread_id"] == thread_id
        assert body["pipeline_id"] == "cursor-sdk-generate"
        assert body["terminal_status"] is None


def test_dispatch_link_lookup_miss(bus_db) -> None:
    with TestClient(_app(bus_db)) as client:
        resp = client.get("/dispatch-links/unknown-exec")
        assert resp.status_code == 404
