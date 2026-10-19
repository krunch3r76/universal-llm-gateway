"""Notice closeout dedupe — LANE CLOSEOUT and cursor-sdk CLOSEOUT (a:39664)."""

from __future__ import annotations

import json

import pytest
from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, create_turn, init_db
from agent_bus_store.notice_closeout_dedupe import find_duplicate_notice_closeout
from bus_watch.lane_closeout import format_closeout_body, format_closeout_subject
from bus_watch.lane_closeout_parse import build_closeout_record
from fastapi.testclient import TestClient
pytestmark = pytest.mark.offline

_SDK_CLOSEOUT_SUBJECT = (
    "cursor-sdk CLOSEOUT 893994c82a61-75be8122 contract=implement caller=conductor-hop"
)


def _store_client(tmp_path) -> TestClient:
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _lane_closeout_body(*, lane: str = "18139", settled: str = "complete") -> str:
    record = build_closeout_record(
        {"id": lane, "slug": "fix", "lifecycle": "completed", "status": "closed"},
        parent_root="17934",
        worker_closeout_text=f"TYPE: CLOSEOUT\nstatus: {settled}\n",
    )
    return format_closeout_body(record)


@pytest.fixture()
def bus_db(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    yield


def test_find_duplicate_lane_closeout_same_settlement(bus_db) -> None:
    create_thread(thread_id="17934", slug="house")
    body = _lane_closeout_body()
    subject = format_closeout_subject(
        build_closeout_record(
            {"id": "18139", "lifecycle": "completed"},
            parent_root="17934",
            worker_closeout_text="status: complete\n",
        )
    )
    create_turn(
        thread_id="17934",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject=subject,
        body=body,
        status="open",
    )
    dup = find_duplicate_notice_closeout(
        "17934",
        from_agent="cursor",
        subject=subject,
        body=body,
    )
    assert dup is not None
    assert dup["turn_number"] == 1


def test_find_duplicate_lane_closeout_allows_settlement_refresh(bus_db) -> None:
    create_thread(thread_id="12286", slug="house")
    first_body = _lane_closeout_body(lane="12527", settled="checks_failed")
    second_body = _lane_closeout_body(lane="12527", settled="shipped")
    subject = format_closeout_subject(
        build_closeout_record(
            {"id": "12527", "lifecycle": "completed"},
            parent_root="12286",
            worker_closeout_text="status: checks_failed\n",
        )
    )
    create_turn(
        thread_id="12286",
        from_agent="cursor",
        to_agent="web-anthropic",
        subject=subject,
        body=first_body,
        status="open",
    )
    assert (
        find_duplicate_notice_closeout(
            "12286",
            from_agent="cursor",
            subject=subject,
            body=second_body,
        )
        is None
    )


def test_send_lane_closeout_replay_burst_single_turn(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    client = _store_client(tmp_path)
    create_thread(thread_id="17934", slug="house")
    body = _lane_closeout_body()
    subject = "LANE CLOSEOUT agent-bus:18139 completed"
    payload = {
        "thread": "17934",
        "from": "cursor",
        "to": "web-anthropic",
        "subject": subject,
        "body": body,
        "tags": ["lane:closeout"],
    }
    first = client.post("/threads/send", json=payload)
    second = client.post("/threads/send", json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json().get("idempotent_replay") is True
    turns = client.get("/turns", params={"thread": "17934"}).json()["turns"]
    assert len(turns) == 1


def test_turns_sdk_closeout_replay_by_dispatch_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.setenv("AGENT_BUS_EVENTS_ENABLED", "false")
    init_db()
    client = _store_client(tmp_path)
    create_thread(thread_id="18139", slug="lane")
    subject = _SDK_CLOSEOUT_SUBJECT
    body = json.dumps({"work_outcome": "shipped", "landed": True})
    payload = {
        "thread": "18139",
        "from": "cursor-sdk",
        "to": "cursor",
        "subject": subject,
        "body": body,
    }
    first = client.post("/turns", json=payload)
    second = client.post("/turns", json=payload)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json().get("idempotent_replay") is True
    turns = client.get("/turns", params={"thread": "18139"}).json()["turns"]
    assert len(turns) == 1
