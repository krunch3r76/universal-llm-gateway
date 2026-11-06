"""HTTP If-Match adapter tests (B4)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.offline


def _seed_auth(client: TestClient) -> None:
    client.post(
        "/entities",
        json={"id": "decision:http-auth", "type": "decision", "name": "auth"},
    )


def test_b4_if_match_required_428(cortex_client: TestClient) -> None:
    _seed_auth(cortex_client)
    resp = cortex_client.post(
        "/concern-aggregate/transition",
        json={
            "concern_id": "concern:http-428",
            "kind": "position_head",
            "record_ref": "rec-428",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:428",
            "material": {},
            "expected_revision": 0,
        },
    )
    assert resp.status_code == 428


def test_b4_weak_etag_412(cortex_client: TestClient) -> None:
    _seed_auth(cortex_client)
    resp = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": 'W/"1"'},
        json={
            "concern_id": "concern:http-412",
            "kind": "position_head",
            "record_ref": "rec-412",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:412",
            "material": {},
        },
    )
    assert resp.status_code == 412


def test_b4_malformed_if_match_400(cortex_client: TestClient) -> None:
    _seed_auth(cortex_client)
    resp = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": "1"},
        json={
            "concern_id": "concern:http-400",
            "kind": "position_head",
            "record_ref": "rec-400",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:400",
            "material": {},
        },
    )
    assert resp.status_code == 400


def test_b4_success_sets_etag(cortex_client: TestClient) -> None:
    _seed_auth(cortex_client)
    resp = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": '"0"'},
        json={
            "concern_id": "concern:http-200",
            "kind": "position_head",
            "record_ref": "rec-200",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:200",
            "material": {"ok": True},
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("etag") == '"1"'
