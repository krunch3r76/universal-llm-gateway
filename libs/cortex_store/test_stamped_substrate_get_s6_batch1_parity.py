"""S6 batch 1: dispatch vs typed GET parity for substrate read ops."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cortex_store._intent_card_test_fixtures import insert_assertion, insert_entity
from cortex_store.dispatch_ops import execute_op

_DISPATCH_ENVELOPE_KEYS = frozenset({"_next", "_hint", "skill_hint"})


def _without_dispatch_envelope(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _DISPATCH_ENVELOPE_KEYS}


def _assert_parity(
    cortex_client: TestClient,
    *,
    op: str,
    dispatch_args: dict[str, Any],
    path: str,
) -> None:
    dispatch_body = _without_dispatch_envelope(execute_op(op, dispatch_args))
    http_resp = cortex_client.get(path)
    assert http_resp.status_code == 200, http_resp.text
    assert dispatch_body == http_resp.json()


@pytest.mark.offline
def test_assertion_get_dispatch_matches_typed_route(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "decision:s6-assertion-get-parity"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="decision")
    assertion_id = insert_assertion(
        migrated_conn,
        entity_id=entity_id,
        claim="S6 batch 1 assertion_get parity fixture.",
        confidence="confirmed",
    )
    _assert_parity(
        cortex_client,
        op="assertion_get",
        dispatch_args={"assertion_id": assertion_id},
        path=f"/assertions/{assertion_id}",
    )


@pytest.mark.offline
def test_assertion_state_dispatch_matches_typed_route(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    entity_id = "decision:s6-assertion-state-parity"
    insert_entity(migrated_conn, entity_id=entity_id, entity_type="decision")
    insert_assertion(
        migrated_conn,
        entity_id=entity_id,
        claim="Confirmed row for assertion_state parity.",
        confidence="confirmed",
    )
    _assert_parity(
        cortex_client,
        op="assertion_state",
        dispatch_args={"entity_id": entity_id},
        path=f"/entities/{entity_id}/assertion-state",
    )


@pytest.mark.offline
def test_assertion_get_does_not_shadow_search_or_activate_routes(
    cortex_client: TestClient,
) -> None:
    """/{assertion_id:int} must not capture static GET siblings (review B1)."""
    search = cortex_client.get("/assertions/search", params={"q": "parity-shadow"})
    assert search.status_code != 422 or "int_parsing" not in search.text
    assert search.status_code == 200

    activate = cortex_client.get(
        "/assertions/activate",
        params={"entity_ids": "decision:shadow-probe"},
    )
    assert activate.status_code != 422 or "int_parsing" not in activate.text
    assert activate.status_code == 200


@pytest.mark.offline
def test_entities_by_content_hash_dispatch_matches_typed_route(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    content_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
    entity_id = "document:s6-content-hash-parity"
    insert_entity(
        migrated_conn,
        entity_id=entity_id,
        entity_type="document",
        name="Content hash parity doc",
    )
    migrated_conn.execute(
        "UPDATE entities SET content_hash = ? WHERE id = ?",
        (content_hash, entity_id),
    )
    migrated_conn.commit()
    _assert_parity(
        cortex_client,
        op="entities_by_content_hash",
        dispatch_args={"content_hash": content_hash},
        path=f"/entities/by-content-hash/{content_hash}",
    )
