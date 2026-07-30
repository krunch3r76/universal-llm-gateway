"""POST /claims/burst — salience slice 3+4 acceptance tests."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.claims_burst import _burst_rank_key
from cortex_store.models.claims_burst import BurstClaimItem

_A20701_CLAIM = (
    "WO 956908029 / lower-payment request — DENIED, confirmed 2026-06-26. "
    "On the 2026-06-26 ~12:30 PM Chase Escalations call (case ECW260413-02188, "
    "rep 'Matthew', who reviewed Janet's notes), Chase stated it is unable to "
    "spread the escrow shortage beyond 12 months and that the request for the "
    "lower payment was DENIED."
)

_A7738_CLAIM = (
    "WO #953902037 — Kaywan's request to extend escrow shortage spread beyond "
    "the standard 12-month RESPA floor — was DENIED on the 2026-04-29 Nell Cruz "
    "callback. Nell stated: 'we are unable to spread the escrow shortage over "
    "12 months.'"
)

_PENDING_WO_CLAIM = (
    "WO #953902037 opened 2026-07-15 — spread extension request pending review "
    "with Chase Escalations."
)

_ENTITY = "account:chase-mortgage-8787"


def _seed_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, "account", entity_id),
    )
    conn.commit()


def _insert_assertion(
    conn: sqlite3.Connection,
    *,
    assertion_id: int,
    claim: str,
    observed_at: str,
    review_status: str = "committed",
    predicate_form: str = "status(account:chase-mortgage-8787, denied, current)",
) -> None:
    claim_hash = compute_claim_hash(_ENTITY, claim)
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "review_status, predicate_form) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, ?, ?, ?)",
        (
            assertion_id,
            _ENTITY,
            claim,
            claim_hash,
            observed_at,
            review_status,
            predicate_form,
        ),
    )
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) VALUES (?, ?, ?)",
        (assertion_id, _ENTITY, claim),
    )
    conn.commit()


@pytest.fixture()
def escrow_fixture(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _ENTITY)
    _insert_assertion(
        migrated_conn,
        assertion_id=20701,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        review_status="committed",
    )
    _insert_assertion(
        migrated_conn,
        assertion_id=7738,
        claim=_A7738_CLAIM,
        observed_at="2026-04-29T17:10:00Z",
        review_status="staged",
    )
    _insert_assertion(
        migrated_conn,
        assertion_id=99001,
        claim=_PENDING_WO_CLAIM,
        observed_at="2026-07-15T10:00:00Z",
        review_status="staged",
        predicate_form="status(account:chase-mortgage-8787, pending, current)",
    )


def _burst_payload(**overrides: object) -> dict:
    payload = {
        "vocabulary": ["spread_extension"],
        "scope_entity_ids": [_ENTITY],
        "include_contradictions": False,
    }
    payload.update(overrides)
    return payload


def test_ac1_burst_returns_terminal_denials_without_denied_query(
    cortex_client: TestClient,
    escrow_fixture: None,
) -> None:
    resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["assertion_id"] for item in body["claims"]}
    assert 20701 in ids
    assert 7738 in ids
    by_id = {item["assertion_id"]: item for item in body["claims"]}
    assert by_id[20701]["terminal"] is True
    assert by_id[7738]["terminal"] is True
    assert by_id[20701]["predicate_form"] == "denied(spread_extension, chase, 2026-06-26)"


def test_ac2_contradiction_pairs_block_request(
    cortex_client: TestClient,
    escrow_fixture: None,
) -> None:
    resp = cortex_client.post(
        "/claims/burst",
        json=_burst_payload(include_contradictions=True),
    )
    assert resp.status_code == 200
    pairs = resp.json()["contradiction_pairs"]
    assert pairs
    blocking = {pair["blocking_assertion_id"] for pair in pairs}
    assert 20701 in blocking
    assert any(
        pair["proposed_predicate_form"] == "request(spread_extension, chase)"
        for pair in pairs
    )


def test_burst_rank_key_terminal_first_despite_large_recency_gap() -> None:
    """Regression: additive +900d bonus let non-terminal rows ~3y newer outrank terminal."""
    old_terminal = datetime(2020, 1, 1, tzinfo=UTC)
    new_pending = datetime(2023, 1, 1, tzinfo=UTC)

    def _item(*, terminal: bool, assertion_id: int) -> BurstClaimItem:
        functor = "denied" if terminal else "pending"
        return BurstClaimItem(
            assertion_id=assertion_id,
            claim="fixture",
            predicate_form=f"{functor}(spread_extension, chase)",
            epistemic_state=None,
            terminal=terminal,
            entity_id=_ENTITY,
            functor=functor,
            action="spread_extension",
            party="chase",
            derivation="fixture",
            claim_excerpt="fixture",
        )

    pairs = [
        (_item(terminal=False, assertion_id=2), new_pending),
        (_item(terminal=True, assertion_id=1), old_terminal),
    ]
    pairs.sort(key=lambda pair: _burst_rank_key(pair[0], pair[1]), reverse=True)
    assert pairs[0][0].assertion_id == 1
    assert pairs[0][0].terminal is True


def test_ac3_terminal_denial_ranks_above_recent_pending(
    cortex_client: TestClient,
    escrow_fixture: None,
) -> None:
    resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    claims = resp.json()["claims"]
    ids = [item["assertion_id"] for item in claims]
    assert ids.index(20701) < ids.index(99001)
    pending = next(item for item in claims if item["assertion_id"] == 99001)
    assert pending["terminal"] is False
    assert pending["functor"] == "pending"


def test_ac4_openapi_schema_exposes_typed_request_fields(
    cortex_client: TestClient,
) -> None:
    schema = cortex_client.get("/openapi.json").json()
    burst_op = schema["paths"]["/claims/burst"]["post"]
    req_schema = burst_op["requestBody"]["content"]["application/json"]["schema"]
    if "$ref" in req_schema:
        ref = req_schema["$ref"].split("/")[-1]
        req_schema = schema["components"]["schemas"][ref]
    assert "vocabulary" in req_schema["properties"]
    assert "scope_entity_ids" in req_schema["properties"]
    assert "include_contradictions" in req_schema["properties"]
    vocab_field = req_schema["properties"]["vocabulary"]
    assert vocab_field["type"] == "array"


def test_ac5_burst_is_read_only(
    cortex_client: TestClient,
    escrow_fixture: None,
    migrated_conn: sqlite3.Connection,
) -> None:
    before = migrated_conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = 20701"
    ).fetchone()[0]
    update_calls: list[dict] = []

    def _track_update(**kwargs: object) -> dict:
        update_calls.append(dict(kwargs))
        return {"error": "blocked in test"}

    with patch(
        "cortex_store.routes.assertions._update._update_assertion_impl",
        side_effect=_track_update,
    ):
        resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    assert update_calls == []
    after = migrated_conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = 20701"
    ).fetchone()[0]
    assert after == before
    assert "status(account:chase-mortgage-8787" in after
