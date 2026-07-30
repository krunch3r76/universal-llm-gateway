"""entity_get terminal_facts — arc 6386 slice 5a acceptance tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.dispatch_ops.ops_entities import _op_entity_get
from cortex_store.terminal_facts import (
    radiate_terminal_facts_scope,
    resolve_terminal_facts_scope,
)

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
_FINANCE_ENTITY = "finance:fixture-mortgage-6386-5a"
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


def _insert_relationship(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    target_id: str,
    rel_type: str,
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO relationship_types (type, description) VALUES (?, ?)",
        (rel_type, "fixture"),
    )
    conn.execute(
        "INSERT INTO relationships "
        "(from_entity, to_entity, type, active, strength, created_at, updated_at) "
        "VALUES (?, ?, ?, 1, 1.0, '2026-07-30T00:00:00Z', '2026-07-30T00:00:00Z')",
        (source_id, target_id, rel_type),
    )
    conn.commit()


@pytest.fixture()
def hub_scope_fixture(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _CASE_ENTITY, entity_type="case")
    _seed_entity(migrated_conn, _ACCOUNT_ENTITY, entity_type="account")
    _seed_entity(migrated_conn, _FINANCE_ENTITY, entity_type="finance")
    _insert_relationship(
        migrated_conn,
        source_id=_FINANCE_ENTITY,
        target_id=_CASE_ENTITY,
        rel_type="involves",
    )
    _insert_relationship(
        migrated_conn,
        source_id=_ACCOUNT_ENTITY,
        target_id=_FINANCE_ENTITY,
        rel_type="related_to",
    )
    _insert_assertion(
        migrated_conn,
        entity_id=_ACCOUNT_ENTITY,
        assertion_id=20701,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
        review_status="committed",
    )


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


def test_case_hub_reaches_account_scoped_denial_via_finance_bridge(
    cortex_client: TestClient,
    hub_scope_fixture: None,
) -> None:
    resp = cortex_client.get(f"/entities/{_CASE_ENTITY}")
    assert resp.status_code == 200
    facts = resp.json()["terminal_facts"]["facts"]
    assert any(item["assertion_id"] == 20701 for item in facts)


def test_resolve_terminal_facts_scope_includes_account_via_finance_bridge(
    migrated_conn: sqlite3.Connection,
    hub_scope_fixture: None,
) -> None:
    scope = resolve_terminal_facts_scope(migrated_conn, _CASE_ENTITY)
    assert _CASE_ENTITY in scope
    assert _ACCOUNT_ENTITY in scope


_SERVICER_ENTITY = "party:chase-fixture-6386"
_HUB_STAR = "case:hub-star-fixture-6386"


@pytest.fixture()
def radiation_two_hop_fixture(migrated_conn: sqlite3.Connection) -> None:
    """Case → finance → servicer with terminal denial on non-hub servicer entity."""
    _seed_entity(migrated_conn, _HUB_STAR, entity_type="case")
    _seed_entity(migrated_conn, _FINANCE_ENTITY, entity_type="finance")
    _seed_entity(migrated_conn, _SERVICER_ENTITY, entity_type="party")
    _insert_relationship(
        migrated_conn,
        source_id=_HUB_STAR,
        target_id=_FINANCE_ENTITY,
        rel_type="involves",
    )
    _insert_relationship(
        migrated_conn,
        source_id=_FINANCE_ENTITY,
        target_id=_SERVICER_ENTITY,
        rel_type="related_to",
    )
    _insert_assertion(
        migrated_conn,
        entity_id=_SERVICER_ENTITY,
        assertion_id=55001,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )


def test_radiation_reaches_non_hub_terminal_fact(
    cortex_client: TestClient,
    radiation_two_hop_fixture: None,
) -> None:
    resp = cortex_client.get(f"/entities/{_HUB_STAR}")
    assert resp.status_code == 200
    facts = resp.json()["terminal_facts"]["facts"]
    assert any(item["assertion_id"] == 55001 for item in facts)
    remote = next(item for item in facts if item["assertion_id"] == 55001)
    assert not remote["entity_id"].startswith(("case:", "account:"))
    assert remote["hop_distance"] == 2
    assert remote["arrival_path"] == [_HUB_STAR, _FINANCE_ENTITY, _SERVICER_ENTITY]


def test_radiated_scope_includes_two_hop_neighbours(
    migrated_conn: sqlite3.Connection,
    radiation_two_hop_fixture: None,
) -> None:
    scope = resolve_terminal_facts_scope(migrated_conn, _HUB_STAR)
    assert _SERVICER_ENTITY in scope
    radiation = radiate_terminal_facts_scope(migrated_conn, _HUB_STAR)
    assert radiation.hop_distances[_SERVICER_ENTITY] == 2


def test_entity_cap_exhaustion_degrades_not_vanishes(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    hub = "case:cap-degrade-fixture-6386"
    _seed_entity(migrated_conn, hub, entity_type="case")
    _insert_assertion(
        migrated_conn,
        entity_id=hub,
        assertion_id=56001,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )
    leaf_ids = [f"todo:cap-leaf-{idx}-6386" for idx in range(60)]
    for leaf_id in leaf_ids:
        _seed_entity(migrated_conn, leaf_id, entity_type="todo")
        _insert_relationship(
            migrated_conn,
            source_id=hub,
            target_id=leaf_id,
            rel_type="references",
        )
    with patch("cortex_store.scope_radiation.HUB_SCOPE_ENTITY_CAP", 5), patch(
        "cortex_store.scope_radiation.HUB_REL_THRESHOLD",
        100,
    ):
        resp = cortex_client.get(f"/entities/{hub}")
    assert resp.status_code == 200
    block = resp.json()["terminal_facts"]
    assert block["scope_truncated"] is True
    assert block["scope_size"] <= 5
    assert any(item["assertion_id"] == 56001 for item in block["facts"])


def test_terminal_facts_use_compact_claim_and_derivation(
    cortex_client: TestClient,
    escrow_case_fixture: None,
) -> None:
    resp = cortex_client.get(f"/entities/{_CASE_ENTITY}")
    fact = resp.json()["terminal_facts"]["facts"][0]
    assert fact["derivation"] == "action_enrichment_template_v0"
    assert fact["machine_derived"] is True
    assert fact["detector_version"] == "action_enrichment_template_v0"
    assert len(fact["claim"]) <= 200
    assert fact.get("claim_excerpt") is None
    assert fact["hop_distance"] == 0
    assert fact["arrival_path"] == [_CASE_ENTITY]


_LIVE_DB = (
    Path("/home/io/.local/share/git-integration-worker/cursor-dispatch-homes")
    / "a41f1f3fb695-78ec3ff3-home"
    / ".cortex"
    / "cortex.db"
)

_FABRICATED_ASSERTION_IDS = frozenset(
    {7910, 8284, 21637, 24734, 23262, 8909, 27045, 23082, 24187}
)

_SCOPE_REGRESSION_FIXTURES = (
    {
        "hub": "case:chase-escrow-flintridge-2026",
        "scope_size": 30,
        "required_assertions": frozenset({20701, 7738, 26054, 12461}),
        "required_predicates": {
            20701: "denied(spread_extension, chase, 2026-06-26)",
            7738: "denied(spread_extension, chase, 2026-04-29)",
            26054: "denied(spread_extension, chase, 2026-06-26)",
            12461: "denied(spread_extension, chase, 2026-04-29)",
        },
        "min_fact_count": 5,
        "fact_count": 5,
    },
    {
        "hub": "account:chase-mortgage-8787",
        "scope_size": 28,
        "required_assertions": frozenset({20701, 7738, 26054, 12461}),
        "required_predicates": {
            20701: "denied(spread_extension, chase, 2026-06-26)",
            7738: "denied(spread_extension, chase, 2026-04-29)",
            26054: "denied(spread_extension, chase, 2026-06-26)",
            12461: "denied(spread_extension, chase, 2026-04-29)",
        },
        "min_fact_count": 4,
        "fact_count": 4,
    },
    {
        "hub": "case:boe19p-flintridge-appeal-2026",
        "scope_size": 50,
        "required_assertions": frozenset(),
        "required_predicates": {},
        "min_fact_count": 0,
        "max_fact_count": 0,
    },
)


def _live_cortex_conn() -> sqlite3.Connection | None:
    if not _LIVE_DB.exists():
        return None
    from cortex_store.db import _connect

    return _connect(_LIVE_DB)


@pytest.mark.integration
@pytest.mark.parametrize("fixture", _SCOPE_REGRESSION_FIXTURES, ids=lambda f: f["hub"])
def test_live_hub_scope_and_fact_count_regression(fixture: dict) -> None:
    from cortex_store.scope_radiation import radiate_scope
    from cortex_store.terminal_facts import resolve_terminal_facts

    conn = _live_cortex_conn()
    if conn is None:
        pytest.skip("live cortex.db unavailable")
    hub = fixture["hub"]
    try:
        row = conn.execute("SELECT 1 FROM entities WHERE id = ?", (hub,)).fetchone()
        if row is None:
            pytest.skip(f"{hub} not in live db")

        scope = radiate_scope(conn, hub)
        block, _ = resolve_terminal_facts(conn, hub)

        assert len(scope.hop_distances) == fixture["scope_size"]
        assert scope.hop_distances[hub] == 0

        if block is None:
            assert fixture["min_fact_count"] == 0
            return

        assert block.scope_size == fixture["scope_size"]
        assert block.fact_count >= fixture["min_fact_count"]
        if "fact_count" in fixture:
            assert block.fact_count == fixture["fact_count"]
        if "max_fact_count" in fixture:
            assert block.fact_count <= fixture["max_fact_count"]

        by_id = {item.assertion_id: item for item in block.facts}
        for assertion_id in fixture["required_assertions"]:
            assert assertion_id in by_id
            expected = fixture["required_predicates"][assertion_id]
            assert by_id[assertion_id].predicate_form == expected

        all_ids = {item.assertion_id for item in block.facts}
        assert _FABRICATED_ASSERTION_IDS.isdisjoint(all_ids)

        if block.capped:
            assert block.facts_dropped == block.fact_count - block.cap
            assert len(block.facts) == block.cap
        else:
            assert block.facts_dropped == 0
            assert len(block.facts) == block.fact_count

        undated = [item for item in block.facts if item.undated]
        dated = [item for item in block.facts if not item.undated]
        if undated and dated:
            first_undated_idx = next(
                idx for idx, item in enumerate(block.facts) if item.undated
            )
            last_dated_idx = max(
                idx for idx, item in enumerate(block.facts) if not item.undated
            )
            assert first_undated_idx > last_dated_idx
    finally:
        conn.close()
