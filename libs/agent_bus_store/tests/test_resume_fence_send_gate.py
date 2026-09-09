"""Send citation gate tests for resume fence."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.resume_fence_store import append_fence_event, mint_fence_id

pytestmark = pytest.mark.offline


@pytest.fixture()
def bus_client(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    create_thread(thread_id="10223", slug="house", tags=["role:root"])
    create_turn(
        thread_id="10223",
        from_agent="cursor",
        to_agent="cursor",
        subject="CHECKPOINT",
        body="agent-bus:10223 only",
        status="open",
    )
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _open_fence() -> str:
    fid = mint_fence_id()
    read_set = {
        "readable": {
            "bus_threads": ["10223"],
            "bus_turns": ["10223#1"],
            "cortex_uris": ["cortex://notes/system/threads/10223-continuity.md"],
            "entities": ["document:10223-continuity"],
            "fs_paths": [],
            "shell": False,
            "mcp_allow": [],
        },
        "citable": {"bus_threads": ["10223"], "git": [], "transcripts": []},
    }
    append_fence_event(
        fence_id=fid,
        root_thread="10223",
        event="armed",
        payload={"source": "test"},
    )
    append_fence_event(
        fence_id=fid,
        root_thread="10223",
        event="poured",
        payload={"read_set": read_set, "bundle_bytes": 10, "seal_status": "sealed"},
    )
    return fid


def test_send_foreign_citation_422(bus_client) -> None:
    fid = _open_fence()
    resp = bus_client.post(
        "/threads/send",
        json={
            "thread": "10223",
            "from": "cursor",
            "to": "cursor",
            "subject": "orientation",
            "body": "Leaked agent-bus:9796 reference",
            "fence_id": fid,
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["reason"] == "resume_fence.foreign_citation"
    assert "agent-bus:9796" in detail["foreign"]


def test_send_clean_releases_fence(bus_client) -> None:
    fid = _open_fence()
    resp = bus_client.post(
        "/threads/send",
        json={
            "thread": "10223",
            "from": "cursor",
            "to": "cursor",
            "subject": "orientation",
            "body": "Clean orientation on agent-bus:10223",
            "fence_id": fid,
        },
    )
    assert resp.status_code == 201
    again = bus_client.post(
        "/threads/send",
        json={
            "thread": "10223",
            "from": "cursor",
            "to": "cursor",
            "subject": "followup",
            "body": "second",
            "fence_id": fid,
        },
    )
    assert again.status_code == 422
    assert again.json()["detail"]["reason"] == "resume_fence.not_open"
