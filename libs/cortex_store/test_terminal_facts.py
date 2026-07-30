"""entity_get terminal_facts — arc 6386 slice 5a acceptance tests."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.dispatch_ops.ops_entities import _op_entity_get

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

_CASE_ENTITY = "case:chase-escrow-fixture-6386-5a"
_ACCOUNT_ENTITY = "account:fixture-mortgage-6386-5a"
_TODO_ENTITY = "todo:fixture-no-terminal-6386-5a"


def _seed_entity(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    entity_type: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, entity_type, entity_id),
    )
    conn.commit()


def _insert_assertion(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    assertion_id: int,
    claim: str,
    observed_at: str,
    valid_from: str | None = None,
    review_status: str = "committed",
    predicate_form: str | None = None,
) -> None:
    claim_hash = compute_claim_hash(entity_id, claim)
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "valid_from, review_status, predicate_form) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, ?, ?, ?, ?)",
        (
            assertion_id,
            entity_id,
            claim,
            claim_hash,
            observed_at,
            valid_from,
            review_status,
            predicate_form,
        ),
    )
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) VALUES (?, ?, ?)",
        (assertion_id, entity_id, claim),
    )
    conn.commit()


@pytest.fixture()
def escrow_case_fixture(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _CASE_ENTITY, entity_type="case")
    _insert_assertion(
        migrated_conn,
        entity_id=_CASE_ENTITY,
        assertion_id=20701,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
        review_status="committed",
    )
    _insert_assertion(
        migrated_conn,
        entity_id=_CASE_ENTITY,
        assertion_id=7738,
        claim=_A7738_CLAIM,
        observed_at="2026-04-29T17:10:00Z",
        valid_from="2026-04-29",
        review_status="staged",
    )
    _insert_assertion(
        migrated_conn,
        entity_id=_CASE_ENTITY,
        assertion_id=99001,
        claim=_PENDING_WO_CLAIM,
        observed_at="2026-07-15T10:00:00Z",
        review_status="staged",
        predicate_form="status(case:chase-escrow-fixture-6386-5a, pending, current)",
    )


def test_falsifier_entity_get_card_returns_terminal_facts_without_vocabulary(
    cortex_client: TestClient,
    escrow_case_fixture: None,
) -> None:
    """Seat supplies no vocabulary; default entity_get still forwards terminal facts."""
    resp = cortex_client.get(f"/entities/{_CASE_ENTITY}")
    assert resp.status_code == 200
    body = resp.json()
    block = body["terminal_facts"]
    facts = block["facts"]
    by_id = {item["assertion_id"]: item for item in facts}
    assert 20701 in by_id
    assert by_id[20701]["predicate_form"] == "denied(spread_extension, chase, 2026-06-26)"
    assert by_id[20701]["terminal"] is True
    assert all(item["terminal"] for item in facts)
    assert 99001 not in by_id


def test_terminal_denial_ranks_above_older_denial_in_terminal_facts(
    cortex_client: TestClient,
    escrow_case_fixture: None,
) -> None:
    resp = cortex_client.get(f"/entities/{_CASE_ENTITY}")
    facts = resp.json()["terminal_facts"]["facts"]
    ids = [item["assertion_id"] for item in facts]
    assert ids.index(20701) < ids.index(7738)


def test_account_hub_receives_terminal_facts(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _ACCOUNT_ENTITY, entity_type="account")
    _insert_assertion(
        migrated_conn,
        entity_id=_ACCOUNT_ENTITY,
        assertion_id=88001,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )
    resp = cortex_client.get(f"/entities/{_ACCOUNT_ENTITY}")
    assert resp.status_code == 200
    facts = resp.json()["terminal_facts"]["facts"]
    assert facts[0]["assertion_id"] == 88001


def test_non_hub_entity_omits_terminal_facts(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _TODO_ENTITY, entity_type="todo")
    _insert_assertion(
        migrated_conn,
        entity_id=_TODO_ENTITY,
        assertion_id=88002,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
    )
    resp = cortex_client.get(f"/entities/{_TODO_ENTITY}")
    assert resp.status_code == 200
    body = resp.json()
    assert "terminal_facts" not in body
    assert "terminal_facts_omitted_reason" not in body


def test_intent_full_also_forwards_terminal_facts(
    cortex_client: TestClient,
    escrow_case_fixture: None,
) -> None:
    resp = cortex_client.get(f"/entities/{_CASE_ENTITY}?intent=full")
    assert resp.status_code == 200
    facts = resp.json()["terminal_facts"]["facts"]
    assert any(item["assertion_id"] == 20701 for item in facts)


def test_dispatch_entity_get_matches_http_wire(
    cortex_client: TestClient,
    escrow_case_fixture: None,
) -> None:
    http_body = cortex_client.get(f"/entities/{_CASE_ENTITY}").json()
    dispatch_body = _op_entity_get(entity_id=_CASE_ENTITY, intent="card")
    assert dispatch_body.get("terminal_facts") == http_body.get("terminal_facts")


def test_terminal_facts_is_read_only(
    cortex_client: TestClient,
    escrow_case_fixture: None,
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
        resp = cortex_client.get(f"/entities/{_CASE_ENTITY}")
    assert resp.status_code == 200
    assert update_calls == []
    after = migrated_conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = 20701"
    ).fetchone()[0]
    assert after == before
