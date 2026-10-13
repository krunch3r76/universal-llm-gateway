"""Offline tests for GET /api/v1/frontier/cdp-legs/{execution_id}."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from systems.frontier_consult.cdp_generate_inflight_ledger import (
    clear_inflight_ledger,
    upsert_inflight_leg,
)
from systems.frontier_consult.cdp_leg_status_route import (
    build_leg_status_payload,
    cdp_leg_status_router,
)

pytestmark = pytest.mark.offline


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(cdp_leg_status_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_ledger() -> None:
    clear_inflight_ledger()


def test_route_missing_leg_returns_found_false(client: TestClient) -> None:
    resp = client.get("/api/v1/frontier/cdp-legs/exec-missing")
    assert resp.status_code == 200
    assert resp.json()["found"] is False


def test_route_open_leg(client: TestClient) -> None:
    upsert_inflight_leg(
        execution_id="exec-open",
        request_id="req-1",
        thread_id="9901",
        pointer_turn=1,
        caller_agent="dispatch",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=2400.0,
    )
    resp = client.get("/api/v1/frontier/cdp-legs/exec-open")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["open"] is True
    assert body["terminal"] is None
    assert body["delivered"] is False


def test_route_closed_leg_terminal_from_proof() -> None:
    upsert_inflight_leg(
        execution_id="exec-closed",
        request_id="req-2",
        thread_id="9901",
        pointer_turn=1,
        caller_agent="dispatch",
        prompt_uri="cortex://p.md",
        model_id="cdp/opus-5",
        max_wall_s=2400.0,
    )
    from systems.frontier_consult.cdp_generate_inflight_ledger import mark_proof_emitted

    mark_proof_emitted("exec-closed")
    with patch(
        "systems.frontier_consult.cdp_leg_status_route.terminal_status_for_execution",
        return_value="completed",
    ):
        payload = build_leg_status_payload("exec-closed")
    assert payload["found"] is True
    assert payload["open"] is False
    assert payload["terminal"] == "completed"
