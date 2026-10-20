"""Endpoint subscription journal: fork 1, fork 2, wildcard, CSE delegate."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.endpoint_subscriptions import resolve_endpoint
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _thread(slug: str) -> str:
    detail = create_thread(thread_id=None, slug=slug)
    assert detail is not None
    return str(detail["id"])


def _sub(client: TestClient, **overrides: object) -> object:
    body = {
        "agent": "grok-bot-gc1",
        "thread_scope": "1",
        "surface": "grok",
        "display": "9",
        "title": "lane",
    }
    body.update(overrides)
    return client.post("/endpoint-subscriptions", json=body)


def test_wildcard_refused_and_cse_delegated(client: TestClient) -> None:
    cursor = _sub(client, agent="cursor", thread_scope="*", surface="cursor", display="8", title="c")
    assert cursor.status_code == 422
    assert cursor.json()["detail"]["code"] == "endpoint_wildcard_refused"
    coord = _sub(client, agent="grok-coordinator-1", thread_scope="*", surface="grok")
    assert coord.status_code == 422
    assert coord.json()["detail"]["code"] == "endpoint_wildcard_refused"
    stage = _sub(client, agent="stage-manager", thread_scope="*", title="house")
    assert stage.status_code == 200
    assert stage.json()["row"]["agent"] == "stage-manager"
    assert stage.json()["row"]["thread_scope"] == "*"
    cse = _sub(client, agent="web-anthropic", thread_scope="1")
    assert cse.status_code == 422
    assert cse.json()["detail"]["code"] == "endpoint_cse_delegated"


def test_maestro_cse_wildcard_subscription(client: TestClient) -> None:
    resp = client.post(
        "/endpoint-subscriptions",
        json={
            "agent": "maestro",
            "thread_scope": "*",
            "surface": "cse",
            "display": "cse",
            "agent_id": "12286",
        },
    )
    assert resp.status_code == 200
    row = resp.json()["row"]
    assert row["agent"] == "maestro"
    assert row["surface"] == "cse"
    assert row["thread_scope"] == "*"
    assert row["agent_id"] == "12286"


def test_cursor_display_8_subscribes(client: TestClient) -> None:
    thread = _thread("cursor-subscribe")
    resp = _sub(
        client,
        agent="cursor",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title="c",
    )
    assert resp.status_code == 200
    row = resp.json()["row"]
    assert row["surface"] == "cursor"
    assert row["display"] == "8"
    listed = client.get("/endpoint-subscriptions", params={"agent": "cursor"})
    subs = listed.json()["subscriptions"]
    assert any(s["thread_scope"] == thread and s["surface"] == "cursor" for s in subs)


def test_exact_beats_wildcard_and_tombstone_falls_through(client: TestClient) -> None:
    thread = _thread("lane")
    assert _sub(client, thread_scope="*", title="wild").status_code == 200
    exact = _sub(client, thread_scope=thread, title="exact")
    assert exact.status_code == 200
    route, row = resolve_endpoint("grok-bot-gc1", thread)  # type: ignore[misc]
    assert route == "exact" and row["title"] == "exact"
    gone = client.post(
        "/endpoint-subscriptions/unsubscribe",
        json={"agent": "grok-bot-gc1", "thread_scope": thread},
    )
    assert gone.status_code == 200 and gone.json()["appended"] is True
    route2, row2 = resolve_endpoint("grok-bot-gc1", thread)  # type: ignore[misc]
    assert route2 == "wildcard" and row2["title"] == "wild"
    insert_turn(thread=thread, from_agent="cursor", to_agent="all", subject="DONE|x", body="b")
    assert resolve_endpoint("all", thread) is None
    assert resolve_endpoint("grok-bot-gc1", thread)[0] == "wildcard"


def test_subscribe_supersedes_prior_agent_id(client: TestClient) -> None:
    thread = _thread("xvfb-supersede")
    first = _sub(
        client,
        agent="cursor",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title=f"{thread} ",
        agent_id="11111111-1111-1111-1111-111111111111",
    )
    assert first.status_code == 200
    second = _sub(
        client,
        agent="cursor",
        thread_scope=thread,
        surface="cursor",
        display="8",
        title=f"{thread} ",
        agent_id="22222222-2222-2222-2222-222222222222",
    )
    assert second.status_code == 200
    assert second.json()["appended"] is True
    prior = second.json()["prior"]
    assert prior is not None
    assert prior["state"] == "unsubscribed"
    assert prior["agent_id"] == "11111111-1111-1111-1111-111111111111"
    assert prior["evidence"] == "superseded_by:22222222-2222-2222-2222-222222222222"
    assert second.json()["prior_subscription_id"] == first.json()["row"]["id"]
    row = second.json()["row"]
    assert row["state"] == "subscribed"
    assert row["agent_id"] == "22222222-2222-2222-2222-222222222222"


def test_identical_resubscribe_and_unsubscribe_idempotent(client: TestClient) -> None:
    thread = _thread("idem")
    with patch("agent_bus_store.routes.threads.subscriptions.emit_endpoint_subscribed") as sub_emit:
        first = _sub(client, thread_scope=thread, bound_by="a")
        assert first.json()["appended"] is True
        again = _sub(client, thread_scope=thread, bound_by="other", evidence="e2")
        assert again.json()["appended"] is False
        changed = _sub(client, thread_scope=thread, title="renamed")
        assert changed.json()["appended"] is True
        assert changed.json()["prior"]["id"] == first.json()["row"]["id"]
        assert sub_emit.call_count == 2
    with patch("agent_bus_store.routes.threads.subscriptions.emit_endpoint_unsubscribed") as unsub:
        once = client.post(
            "/endpoint-subscriptions/unsubscribe",
            json={"agent": "grok-bot-gc1", "thread_scope": thread},
        )
        twice = client.post(
            "/endpoint-subscriptions/unsubscribe",
            json={"agent": "grok-bot-gc1", "thread_scope": thread},
        )
        assert once.json()["appended"] is True
        assert twice.json()["appended"] is False
        assert unsub.call_count == 1
