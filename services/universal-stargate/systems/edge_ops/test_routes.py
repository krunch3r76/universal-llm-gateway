"""HTTP surface: the gate refuses by default, and edge identity is enforced."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.edge_ops import config, runtime
from systems.edge_ops.auth import require_internal_caller
from systems.edge_ops.model import (
    CODE_DISABLED,
    CODE_IDENTITY_REFUSED,
    CODE_LEASE_MISMATCH,
    CODE_NOT_STARTED,
    CODE_TERMINAL,
)
from systems.edge_ops.routes import router
from systems.edge_ops.store import EdgeOpsJournal

from .conftest import LEASE_TTL_SECONDS, NEVER_KIND, ManualClock

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
        (
            "post",
            "/edge/ops/edgeop-x/events",
            {"type": "started", "lease_id": "lease-x"},
        ),
        ("post", "/edge/ops/edgeop-x/result", {"result": {}, "lease_id": "lease-x"}),
        ("post", "/edge/ops/edgeop-x/release", {"lease_id": "lease-x"}),
        (
            "get",
            "/edge/ops/edgeop-x/directives",
            {"lease_id": "lease-x", "after": 0, "wait": 0},
        ),
    )
    for method, path, body in calls:
        kwargs: dict[str, object] = {"headers": EDGE_HEADERS}
        if body is not None:
            if method == "get":
                kwargs["params"] = body
            else:
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
    lease_id = granted.json()["lease_id"]

    started = client.post(
        f"/edge/ops/{op_id}/events",
        json={"type": "started", "lease_id": lease_id},
        headers=EDGE_HEADERS,
    )
    assert started.json()["status"] == "started"

    posted = client.post(
        f"/edge/ops/{op_id}/result",
        json={
            "result": {"ok": True, "pong": True},
            "outcome": "completed",
            "lease_id": lease_id,
        },
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


def test_directives_answer_204_to_the_lease_holder_in_a1(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    lease_body = {"kinds": ["ping"], "wait_s": 0}
    assert (
        client.post("/edge/leases", json=lease_body, headers=EDGE_HEADERS).status_code
        == 204
    )
    admitted = client.post(
        "/edge/ops",
        json={"kind": "ping", "op": "ping", "idempotency_key": "k1"},
    )
    assert admitted.status_code == 202
    op_id = admitted.json()["op_id"]
    granted = client.post("/edge/leases", json=lease_body, headers=EDGE_HEADERS)
    assert granted.status_code == 200
    lease_id = granted.json()["lease_id"]

    response = client.get(
        f"/edge/ops/{op_id}/directives",
        params={"after": 0, "wait": 0, "lease_id": lease_id},
        headers=EDGE_HEADERS,
    )
    assert response.status_code == 204


def test_directives_refuse_a_configured_edge_that_holds_no_lease(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``edge-b`` authenticates and still may not attach to ``edge-a``'s op."""
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    lease_body = {"kinds": ["ping"], "wait_s": 0}
    assert (
        client.post("/edge/leases", json=lease_body, headers=EDGE_HEADERS).status_code
        == 204
    )
    admitted = client.post(
        "/edge/ops",
        json={"kind": "ping", "op": "ping", "idempotency_key": "k1"},
    )
    op_id = admitted.json()["op_id"]
    granted = client.post("/edge/leases", json=lease_body, headers=EDGE_HEADERS)
    assert granted.status_code == 200
    lease_id = granted.json()["lease_id"]

    response = client.get(
        f"/edge/ops/{op_id}/directives",
        params={"after": 0, "wait": 0, "lease_id": lease_id},
        headers={"X-Edge-Id": "edge-b", "Authorization": "Bearer other"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == CODE_LEASE_MISMATCH


def test_a_never_kind_result_before_started_is_refused_over_http(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The protocol refusal reaches the caller in the canonical envelope."""
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    lease_body = {"kinds": [NEVER_KIND], "wait_s": 0}
    idle = client.post("/edge/leases", json=lease_body, headers=EDGE_HEADERS)
    assert idle.status_code == 204

    admitted = client.post(
        "/edge/ops",
        json={"kind": NEVER_KIND, "op": NEVER_KIND, "idempotency_key": "k-never"},
    )
    assert admitted.status_code == 202
    op_id = admitted.json()["op_id"]

    granted = client.post("/edge/leases", json=lease_body, headers=EDGE_HEADERS)
    assert granted.status_code == 200
    assert granted.json()["redelivery"] == "never"
    lease_id = granted.json()["lease_id"]

    refused = client.post(
        f"/edge/ops/{op_id}/result",
        json={"result": {"ok": True}, "outcome": "completed", "lease_id": lease_id},
        headers=EDGE_HEADERS,
    )
    assert refused.status_code == 409
    envelope = refused.json()
    assert set(envelope) == {"error"}
    assert set(envelope["error"]) == {"code", "message"}
    assert envelope["error"]["code"] == CODE_NOT_STARTED
    assert "started" in envelope["error"]["message"]

    record = client.get(f"/edge/ops/{op_id}")
    assert record.json()["status"] == "leased"
    assert record.json()["result"] is None


def test_a_started_ack_on_an_expired_op_is_refused_over_http(
    client: TestClient,
    clock: ManualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The late ack reaches the edge as 409 in the canonical envelope."""
    monkeypatch.setenv(config.ENABLED_ENV_KEY, "true")
    lease_body = {"kinds": [NEVER_KIND], "wait_s": 0}
    assert (
        client.post("/edge/leases", json=lease_body, headers=EDGE_HEADERS).status_code
        == 204
    )
    admitted = client.post(
        "/edge/ops",
        json={
            "kind": NEVER_KIND,
            "op": NEVER_KIND,
            "idempotency_key": "k-never",
            "deadline_s": 5.0,
        },
    )
    assert admitted.status_code == 202
    op_id = admitted.json()["op_id"]
    granted = client.post("/edge/leases", json=lease_body, headers=EDGE_HEADERS)
    assert granted.status_code == 200
    stale_lease_id = granted.json()["lease_id"]

    clock.advance(LEASE_TTL_SECONDS + 1.0)
    refused = client.post(
        f"/edge/ops/{op_id}/events",
        json={"type": "started", "lease_id": stale_lease_id},
        headers=EDGE_HEADERS,
    )
    assert refused.status_code == 409
    envelope = refused.json()
    assert set(envelope) == {"error"}
    assert set(envelope["error"]) == {"code", "message"}
    assert envelope["error"]["code"] == CODE_TERMINAL

    record = client.get(f"/edge/ops/{op_id}")
    assert record.json()["status"] == "expired"
    assert record.json()["started_at_epoch"] is None
