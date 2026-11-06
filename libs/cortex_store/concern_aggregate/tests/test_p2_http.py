"""HTTP If-Match adapter tests (B4)."""

from __future__ import annotations

import pytest
from cortex_store.concern_aggregate.tests.conftest import append_position_head
from cortex_store.dispatch_ops.ops_concern_aggregate import _op_concern_transition
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
        },
    )
    assert resp.status_code == 428


def test_b4_empty_if_match_428(cortex_client: TestClient) -> None:
    _seed_auth(cortex_client)
    resp = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": "  "},
        json={
            "concern_id": "concern:http-empty",
            "kind": "position_head",
            "record_ref": "rec-empty",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:empty",
            "material": {},
        },
    )
    assert resp.status_code == 428


def test_b4_wildcard_on_revision_zero_412(cortex_client: TestClient) -> None:
    _seed_auth(cortex_client)
    resp = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": "*"},
        json={
            "concern_id": "concern:http-star",
            "kind": "position_head",
            "record_ref": "rec-star",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:star",
            "material": {},
        },
    )
    assert resp.status_code == 412
    assert resp.json()["detail"]["error"] == "precondition_failed"


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


def test_b4_stale_if_match_412(cortex_client: TestClient) -> None:
    _seed_auth(cortex_client)
    first = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": '"0"'},
        json={
            "concern_id": "concern:http-stale",
            "kind": "position_head",
            "record_ref": "rec-first",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:stale-1",
            "material": {"n": 1},
        },
    )
    assert first.status_code == 200
    second = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": '"1"'},
        json={
            "concern_id": "concern:http-stale",
            "kind": "position_head",
            "record_ref": "rec-second",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:stale-2",
            "material": {"n": 2},
        },
    )
    assert second.status_code == 200
    stale = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": '"1"'},
        json={
            "concern_id": "concern:http-stale",
            "kind": "position_head",
            "record_ref": "rec-third",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:stale-3",
            "material": {"n": 3},
        },
    )
    assert stale.status_code == 412
    assert stale.json()["detail"]["error"] == "precondition_failed"
    assert stale.json()["detail"]["current_head"]["revision"] == 2


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


def test_b2_http_replay_etag_matches_head(
    cortex_client: TestClient,
    migrated_db_conn,
) -> None:
    """After head advances, replay returns replayed body and ETag == current head."""
    cid = "concern:http-b2"
    migrated_db_conn.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
        ("decision:http-auth", "decision", "auth"),
    )
    migrated_db_conn.commit()
    append_position_head(
        migrated_db_conn,
        cid,
        record_ref="rec-b2-1",
        write_intent_id="wi:http-b2",
        material={"step": 1},
        authorized_by="decision:http-auth",
    )
    first = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": '"1"'},
        json={
            "concern_id": cid,
            "kind": "position_head",
            "record_ref": "rec-b2-2",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:http-b2-adv",
            "material": {"step": 2},
        },
    )
    assert first.status_code == 200
    replay = cortex_client.post(
        "/concern-aggregate/transition",
        headers={"If-Match": '"0"'},
        json={
            "concern_id": cid,
            "kind": "position_head",
            "record_ref": "rec-b2-ignore",
            "authorized_by": "decision:http-auth",
            "write_intent_id": "wi:http-b2",
            "material": {"step": 1},
        },
    )
    assert replay.status_code == 200
    body = replay.json()
    assert body.get("replayed") is True
    assert body["revision"] == 1
    assert replay.headers.get("etag") == '"2"'


def test_mcp_dispatch_position_head_without_if_match(
    concern_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MCP uses dispatch op directly — no HTTP 428 without If-Match."""
    from cortex_store import db as cortex_db_mod

    db_path = concern_db.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr(cortex_db_mod, "_CORTEX_DB", db_path)
    out = _op_concern_transition(
        concern_id="concern:mcp-native",
        kind="position_head",
        record_ref="rec-mcp",
        authorized_by="decision:test-auth",
        expected_revision=0,
        write_intent_id="wi:mcp",
        material={"via": "mcp"},
        emit_advisory=False,
    )
    assert "error" not in out or out.get("revision") is not None
    assert out.get("revision") == 1
