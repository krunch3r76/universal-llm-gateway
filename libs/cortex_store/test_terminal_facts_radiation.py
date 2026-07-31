"""Scope radiation + appeal vocabulary fixtures (arc 6386 terminal-facts leg)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from predicate_form.action_enrichment import enrich_action_predicate_from_claim

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.scope_radiation import (
    HUB_REL_THRESHOLD,
    radiate_scope,
)
from cortex_store.terminal_facts import (
    resolve_terminal_facts_scope,
)

_A20701_CLAIM = (
    "WO 956908029 / lower-payment request — DENIED, confirmed 2026-06-26. "
    "On the 2026-06-26 ~12:30 PM Chase Escalations call (case ECW260413-02188, "
    "rep 'Matthew', who reviewed Janet's notes), Chase stated it is unable to "
    "spread the escrow shortage beyond 12 months and that the request for the "
    "lower payment was DENIED."
)

_AAB_DENIAL_CLAIM = (
    "DENIAL RECEIVED — On 2026-06-05 Dr. Amber Green emailed four "
    "'Invalid – Closed' letters from the SCC Assessment Appeals Board: all four "
    "BOE-305-AH applications were closed without reinstatement."
)

_DENSE_ROOT = "case:dense-root-fixture-6386"
_ACCOUNT_BRIDGE = "account:bridge-fixture-6386"
_FINANCE_BRIDGE = "finance:bridge-fixture-6386"
_RULE_BLOCKED = "rule:infrastructure-fixture-6386"
_DOC_ALLOWED = "document:evidence-fixture-6386"
_TRANSCRIPT_BLOCKED = "transcript:call-fixture-6386"
_ORG_PARTY = "org:chase-fixture-6386"
_ORG_LEAF = "todo:org-leaf-fixture-6386"
_PERSON_PARTY = "person:assessor-fixture-6386"
_PERSON_LEAF = "document:person-leaf-fixture-6386"

_LIVE_DB = (
    Path("/home/io/.local/share/git-integration-worker/cursor-dispatch-homes")
    / "a41f1f3fb695-78ec3ff3-home"
    / ".cortex"
    / "cortex.db"
)


def _seed_entity(conn: sqlite3.Connection, entity_id: str, *, entity_type: str) -> None:
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
) -> None:
    claim_hash = compute_claim_hash(entity_id, claim)
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "valid_from, review_status) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, ?, ?, 'committed')",
        (assertion_id, entity_id, claim, claim_hash, observed_at, valid_from),
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
def dense_root_fixture(migrated_conn: sqlite3.Connection) -> None:
    """Hub with degree above threshold must still radiate from depth 0."""
    _seed_entity(migrated_conn, _DENSE_ROOT, entity_type="case")
    _seed_entity(migrated_conn, _ACCOUNT_BRIDGE, entity_type="account")
    _seed_entity(migrated_conn, _FINANCE_BRIDGE, entity_type="finance")
    for idx in range(HUB_REL_THRESHOLD + 5):
        leaf = f"todo:dense-leaf-{idx}-6386"
        _seed_entity(migrated_conn, leaf, entity_type="todo")
        _insert_relationship(
            migrated_conn,
            source_id=_DENSE_ROOT,
            target_id=leaf,
            rel_type="related_to",
        )
    _insert_relationship(
        migrated_conn,
        source_id=_DENSE_ROOT,
        target_id=_FINANCE_BRIDGE,
        rel_type="involves",
    )
    _insert_relationship(
        migrated_conn,
        source_id=_FINANCE_BRIDGE,
        target_id=_ACCOUNT_BRIDGE,
        rel_type="related_to",
    )
    _insert_assertion(
        migrated_conn,
        entity_id=_ACCOUNT_BRIDGE,
        assertion_id=20701,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )


@pytest.fixture()
def path_bounds_fixture(migrated_conn: sqlite3.Connection) -> None:
    hub = "case:path-bounds-fixture-6386"
    _seed_entity(migrated_conn, hub, entity_type="case")
    for entity_id, entity_type in (
        (_RULE_BLOCKED, "rule"),
        (_DOC_ALLOWED, "document"),
        (_TRANSCRIPT_BLOCKED, "transcript"),
    ):
        _seed_entity(migrated_conn, entity_id, entity_type=entity_type)
    _insert_relationship(
        migrated_conn,
        source_id=hub,
        target_id=_RULE_BLOCKED,
        rel_type="requires",
    )
    _insert_relationship(
        migrated_conn,
        source_id=hub,
        target_id=_DOC_ALLOWED,
        rel_type="evidence_for",
    )
    _insert_relationship(
        migrated_conn,
        source_id=hub,
        target_id=_TRANSCRIPT_BLOCKED,
        rel_type="references",
    )


@pytest.fixture()
def party_non_traversal_fixture(migrated_conn: sqlite3.Connection) -> None:
    hub = "case:party-fixture-6386"
    _seed_entity(migrated_conn, hub, entity_type="case")
    _seed_entity(migrated_conn, _ORG_PARTY, entity_type="org")
    _seed_entity(migrated_conn, _ORG_LEAF, entity_type="todo")
    _seed_entity(migrated_conn, _PERSON_PARTY, entity_type="person")
    _seed_entity(migrated_conn, _PERSON_LEAF, entity_type="document")
    _insert_relationship(
        migrated_conn,
        source_id=hub,
        target_id=_ORG_PARTY,
        rel_type="related_to",
    )
    _insert_relationship(
        migrated_conn,
        source_id=_ORG_PARTY,
        target_id=_ORG_LEAF,
        rel_type="related_to",
    )
    _insert_relationship(
        migrated_conn,
        source_id=hub,
        target_id=_PERSON_PARTY,
        rel_type="related_to",
    )
    _insert_relationship(
        migrated_conn,
        source_id=_PERSON_PARTY,
        target_id=_PERSON_LEAF,
        rel_type="references",
    )
    _insert_assertion(
        migrated_conn,
        entity_id=_ORG_PARTY,
        assertion_id=88010,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )


@pytest.fixture()
def appeal_hub_fixture(migrated_conn: sqlite3.Connection) -> str:
    hub = "case:appeal-hub-fixture-6386"
    _seed_entity(migrated_conn, hub, entity_type="case")
    _insert_assertion(
        migrated_conn,
        entity_id=hub,
        assertion_id=20699,
        claim=_AAB_DENIAL_CLAIM,
        observed_at="2026-06-05T12:00:00Z",
        valid_from="2026-06-05",
    )
    return hub


def test_dense_root_radiates_past_degree_guard(
    migrated_conn: sqlite3.Connection,
    dense_root_fixture: None,
) -> None:
    scope = resolve_terminal_facts_scope(migrated_conn, _DENSE_ROOT)
    assert len(scope) > 1
    assert _ACCOUNT_BRIDGE in scope


def test_dense_root_terminal_facts_include_account_denial(
    cortex_client: TestClient,
    dense_root_fixture: None,
) -> None:
    resp = cortex_client.get(f"/entities/{_DENSE_ROOT}")
    assert resp.status_code == 200
    block = resp.json()["terminal_facts"]
    assert block["scope_size"] > 1
    ids = {item["assertion_id"] for item in block["facts"]}
    assert 20701 in ids


def test_infrastructure_spoke_excluded_document_evidence_included(
    migrated_conn: sqlite3.Connection,
    path_bounds_fixture: None,
) -> None:
    hub = "case:path-bounds-fixture-6386"
    scope = resolve_terminal_facts_scope(migrated_conn, hub)
    assert _RULE_BLOCKED not in scope
    assert _TRANSCRIPT_BLOCKED not in scope
    assert _DOC_ALLOWED in scope


def test_party_node_in_scope_without_neighbour_expansion(
    migrated_conn: sqlite3.Connection,
    party_non_traversal_fixture: None,
) -> None:
    hub = "case:party-fixture-6386"
    scope = resolve_terminal_facts_scope(migrated_conn, hub)
    assert _ORG_PARTY in scope
    assert _PERSON_PARTY in scope
    assert _ORG_LEAF not in scope
    assert _PERSON_LEAF not in scope


def test_party_assertions_collected_in_terminal_facts(
    cortex_client: TestClient,
    party_non_traversal_fixture: None,
) -> None:
    resp = cortex_client.get("/entities/case:party-fixture-6386")
    facts = resp.json()["terminal_facts"]["facts"]
    assert any(item["assertion_id"] == 88010 for item in facts)


def test_cap_exhaustion_surfaces_scope_metadata(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    hub = "case:cap-meta-fixture-6386"
    _seed_entity(migrated_conn, hub, entity_type="case")
    _insert_assertion(
        migrated_conn,
        entity_id=hub,
        assertion_id=56001,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )
    for idx in range(60):
        leaf = f"todo:cap-meta-leaf-{idx}-6386"
        _seed_entity(migrated_conn, leaf, entity_type="todo")
        _insert_relationship(
            migrated_conn,
            source_id=hub,
            target_id=leaf,
            rel_type="related_to",
        )
    from unittest.mock import patch

    with patch("cortex_store.scope_radiation.HUB_SCOPE_ENTITY_CAP", 5), patch(
        "cortex_store.scope_radiation.HUB_REL_THRESHOLD",
        100,
    ):
        resp = cortex_client.get(f"/entities/{hub}")
    block = resp.json()["terminal_facts"]
    assert block["scope_truncated"] is True
    assert block["scope_size"] <= 5
    assert block["scope_cap"] == 5


def test_terminal_facts_label_machine_derived(
    cortex_client: TestClient,
    appeal_hub_fixture: str,
) -> None:
    resp = cortex_client.get(f"/entities/{appeal_hub_fixture}")
    body = resp.json()
    block = body.get("terminal_facts")
    if block is None:
        return
    assert block["detector_version"] == "action_enrichment_template_v0"
    for fact in block["facts"]:
        assert fact["machine_derived"] is True
        assert fact["detector_version"] == "action_enrichment_template_v0"
        assert fact["epistemic_state"] == "committed"


def test_appeal_vocabulary_does_not_surface_fabricated_denials(
    cortex_client: TestClient,
    appeal_hub_fixture: str,
) -> None:
    resp = cortex_client.get(f"/entities/{appeal_hub_fixture}")
    block = resp.json().get("terminal_facts")
    if block is None:
        return
    assert not any(
        item["action"] in {"reinstatement", "rfr_filing", "late_filing", "appeal"}
        for item in block["facts"]
    )


def test_appeal_enrichment_emits_denied_assessment_appeal_application() -> None:
    preview = enrich_action_predicate_from_claim(
        _AAB_DENIAL_CLAIM,
        "case:boe19p-flintridge-appeal-2026",
        assertion_id=20699,
        valid_from="2026-06-05",
        domain="tax_appeal",
    )
    assert preview is not None
    assert preview.predicate_form == (
        "denied(assessment_appeal_application, aab, 2026-06-05)"
    )
    assert preview.party == "aab"
    assert preview.party != "boe19p"


@pytest.mark.integration
@pytest.mark.parametrize(
    "hub,min_scope",
    [
        ("case:chase-escrow-flintridge-2026", 2),
        ("case:boe19p-flintridge-appeal-2026", 2),
        ("account:chase-mortgage-8787", 2),
    ],
)
def test_live_hub_scope_regression(hub: str, min_scope: int) -> None:
    if not _LIVE_DB.exists():
        pytest.skip("live cortex.db unavailable")
    from cortex_store.db import _connect

    conn = _connect(_LIVE_DB)
    try:
        row = conn.execute(
            "SELECT 1 FROM entities WHERE id = ?",
            (hub,),
        ).fetchone()
        if row is None:
            pytest.skip(f"{hub} not in live db")
        scope = radiate_scope(conn, hub)
        assert len(scope.hop_distances) >= min_scope
    finally:
        conn.close()
