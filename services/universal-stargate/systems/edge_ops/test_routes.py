"""HTTP surface: the gate refuses by default, and edge identity is enforced."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.edge_ops import config, runtime
from systems.edge_ops.auth import require_internal_caller
from systems.edge_ops.model import CODE_DISABLED, CODE_IDENTITY_REFUSED
from systems.edge_ops.routes import router
from systems.edge_ops.store import EdgeOpsJournal

EDGE_ID = "edge-a"
EDGE_TOKEN = "test-edge-token"
EDGE_HEADERS = {"X-Edge-Id": EDGE_ID, "Authorization": f"Bearer {EDGE_TOKEN}"}


@pytest.fixture
def client(
    journal: EdgeOpsJournal,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """A FastAPI app carrying only the edge router, on the test journal."""
    monkeypatch.setenv(
        config.EDGE_TOKENS_ENV_KEY, f"{EDGE_ID}:{EDGE_TOKEN},edge-b:other"
    )
    runtime.set_default_journal(journal)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_internal_caller] = lambda: {"client_id": "test"}
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        runtime.set_default_journal(None)


def test_every_route_refuses_while_the_flag_is_off(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(config.ENABLED_ENV_KEY, raising=False)
    calls = (
        ("post", "/edge/ops", {"kind": "ping", "op": "ping", "idempotency_key": "k"}),
        ("post", "/edge/leases", {"kinds": ["ping"], "wait_s": 0}),
        ("get", "/edge/ops/edgeop-x", None),
        ("get", "/edge/ops/edgeop-x/result", None),
        ("post", "/edge/ops/edgeop-x/events", {"type": "started"}),
        ("post", "/edge/ops/edgeop-x/result", {"result": {}}),
        ("post", "/edge/ops/edgeop-x/release", {}),
        ("get", "/edge/ops/edgeop-x/directives", None),
    )
    for method, path, body in calls:
        kwargs: dict[str, object] = {"headers": EDGE_HEADERS}
        if body is not None:
            kwargs["json"] = body
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 503, path
        assert response.json()["error"]["code"] == CODE_DISABLED, path


def test_an_unknown_edge_identity_is_refused(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    response = client.post(
        "/edge/leases",
        json={"kinds": ["ping"], "wait_s": 0},
        headers={"X-Edge-Id": "edge-z", "Authorization": "Bearer nope"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == CODE_IDENTITY_REFUSED


def test_a_wrong_token_for_a_known_edge_is_refused(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    response = client.post(
        "/edge/leases",
        json={"kinds": ["ping"], "wait_s": 0},
        headers={"X-Edge-Id": EDGE_ID, "Authorization": "Bearer wrong"},
    )
    assert response.status_code == 403


def test_a_body_edge_id_must_match_the_authenticated_identity(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    response = client.post(
        "/edge/leases",
        json={"kinds": ["ping"], "edge_id": "edge-b", "wait_s": 0},
        headers=EDGE_HEADERS,
    )
    assert response.status_code == 403


def test_admit_lease_and_result_over_http(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    idle = client.post(
        "/edge/leases", json={"kinds": ["ping"], "wait_s": 0}, headers=EDGE_HEADERS
    )
    assert idle.status_code == 204

    admitted = client.post(
        "/edge/ops",
        json={"kind": "ping", "op": "ping", "idempotency_key": "k1", "args": {}},
    )
    assert admitted.status_code == 202
    op_id = admitted.json()["op_id"]

    granted = client.post(
        "/edge/leases", json={"kinds": ["ping"], "wait_s": 0}, headers=EDGE_HEADERS
    )
    assert granted.status_code == 200
    assert granted.json()["op_id"] == op_id
    assert granted.json()["redelivery"] == "safe"

    started = client.post(
        f"/edge/ops/{op_id}/events", json={"type": "started"}, headers=EDGE_HEADERS
    )
    assert started.json()["status"] == "started"

    posted = client.post(
        f"/edge/ops/{op_id}/result",
        json={"result": {"ok": True, "pong": True}, "outcome": "completed"},
        headers=EDGE_HEADERS,
    )
    assert posted.json()["status"] == "completed"

    awaited = client.get(f"/edge/ops/{op_id}/result", params={"wait": 0})
    assert awaited.json() == {"ok": True, "pong": True, "op_id": op_id}

    record = client.get(f"/edge/ops/{op_id}")
    assert record.json()["status"] == "completed"


def test_admit_without_a_present_edge_fails_fast(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    response = client.post(
        "/edge/ops",
        json={"kind": "ping", "op": "ping", "idempotency_key": "k1"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "edge_unavailable"


def test_directives_answer_204_in_a1(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    response = client.get(
        "/edge/ops/edgeop-x/directives",
        params={"after": 0, "wait": 0},
        headers=EDGE_HEADERS,
    )
    assert response.status_code == 204
