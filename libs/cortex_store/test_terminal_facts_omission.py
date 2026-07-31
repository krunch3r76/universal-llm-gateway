"""Terminal facts omission-reason contract — arc 6386 §6a acceptance tests."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from predicate_form.action_vocabulary import ACTION_VOCAB_BY_DOMAIN, ACTION_VOCAB_V0

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.models.claims_burst import (
    BurstClaimItem,
    BurstDisclosure,
    ClaimsBurstResponse,
)
from cortex_store.models.terminal_facts import (
    TERMINAL_FACTS_UNREACHABLE_REASONS,
    TerminalFactsOmissionReason,
)
from cortex_store.terminal_facts import resolve_terminal_facts

_A20701_CLAIM = (
    "WO 956908029 / lower-payment request — DENIED, confirmed 2026-06-26. "
    "On the 2026-06-26 ~12:30 PM Chase Escalations call (case ECW260413-02188, "
    "rep 'Matthew', who reviewed Janet's notes), Chase stated it is unable to "
    "spread the escrow shortage beyond 12 months and that the request for the "
    "lower payment was DENIED."
)

_PENDING_WO_CLAIM = (
    "WO #953902037 opened 2026-07-15 — spread extension request pending review "
    "with Chase Escalations."
)

_AAB_HUB = "case:boe19p-flintridge-appeal-fixture-6386-6a"
_AAB_FINANCE = "finance:aab-bridge-fixture-6386-6a"
_AAB_ACCOUNT = "account:chase-mortgage-aab-bridge-6386-6a"
_UNRECOGNIZED_HUB = "case:unknown-vendor-hub-6386-6a"
_MORTGAGE_HUB = "case:chase-escrow-omission-fixture-6386-6a"
_EMPTY_SCOPE_HUB = "case:chase-escrow-empty-scope-6386-6a"
_NON_TERMINAL_HUB = "case:chase-escrow-non-terminal-6386-6a"


def _burst_response(claims: list[BurstClaimItem]) -> ClaimsBurstResponse:
    vocab = sorted(ACTION_VOCAB_V0)
    return ClaimsBurstResponse(
        vocabulary=vocab,
        scope_entity_ids=["fixture:scope"],
        mode="pre_speak",
        claims=claims,
        contradiction_pairs=[],
        disclosure=BurstDisclosure(
            rows_scanned=len(claims),
            rows_returned=len(claims),
            rows_dropped_total=0,
            drops=[],
            vocabulary_requested=vocab,
            vocabulary_accepted=vocab,
            vocabulary_rejected=[],
            detector_version="fixture",
            disclosure_version=1,
        ),
    )


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
def aab_mortgage_bridge_fixture(migrated_conn: sqlite3.Connection) -> None:
    """AAB-shaped hub whose radiated scope contains a mortgage terminal denial."""
    _seed_entity(migrated_conn, _AAB_HUB, entity_type="case")
    _seed_entity(migrated_conn, _AAB_FINANCE, entity_type="finance")
    _seed_entity(migrated_conn, _AAB_ACCOUNT, entity_type="account")
    _insert_relationship(
        migrated_conn,
        source_id=_AAB_FINANCE,
        target_id=_AAB_HUB,
        rel_type="involves",
    )
    _insert_relationship(
        migrated_conn,
        source_id=_AAB_ACCOUNT,
        target_id=_AAB_FINANCE,
        rel_type="related_to",
    )
    _insert_assertion(
        migrated_conn,
        entity_id=_AAB_ACCOUNT,
        assertion_id=66001,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )


@pytest.fixture()
def empty_scope_hub_fixture(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _EMPTY_SCOPE_HUB, entity_type="case")


@pytest.fixture()
def non_terminal_hub_fixture(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _NON_TERMINAL_HUB, entity_type="case")
    _insert_assertion(
        migrated_conn,
        entity_id=_NON_TERMINAL_HUB,
        assertion_id=66002,
        claim=_PENDING_WO_CLAIM,
        observed_at="2026-07-15T10:00:00Z",
        predicate_form=f"status({_NON_TERMINAL_HUB}, pending, current)",
    )


def test_ac2_aab_hub_empty_vocab_short_circuits_without_false_reason(
    migrated_conn: sqlite3.Connection,
    aab_mortgage_bridge_fixture: None,
) -> None:
    """Headline: empty tax_appeal vocab must not emit outside-vocabulary reason."""
    empty_tax = frozenset()
    patched_domains = {**ACTION_VOCAB_BY_DOMAIN, "tax_appeal": empty_tax}
    with patch(
        "cortex_store.terminal_facts.ACTION_VOCAB_BY_DOMAIN",
        patched_domains,
    ):
        block, reason = resolve_terminal_facts(migrated_conn, _AAB_HUB)
    assert block is None
    assert reason is None


def test_ac2_entity_get_aab_hub_has_no_omitted_reason_key(
    cortex_client: TestClient,
    aab_mortgage_bridge_fixture: None,
) -> None:
    empty_tax = frozenset()
    patched_domains = {**ACTION_VOCAB_BY_DOMAIN, "tax_appeal": empty_tax}
    with patch(
        "cortex_store.terminal_facts.ACTION_VOCAB_BY_DOMAIN",
        patched_domains,
    ):
        resp = cortex_client.get(f"/entities/{_AAB_HUB}")
    assert resp.status_code == 200
    body = resp.json()
    assert "terminal_facts" not in body
    assert "terminal_facts_omitted_reason" not in body


def test_ac3_hub_domain_unrecognized(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _UNRECOGNIZED_HUB, entity_type="case")
    block, reason = resolve_terminal_facts(migrated_conn, _UNRECOGNIZED_HUB)
    assert block is None
    assert reason == TerminalFactsOmissionReason.hub_domain_unrecognized.value


def test_ac3_burst_returned_no_claims(
    migrated_conn: sqlite3.Connection,
    empty_scope_hub_fixture: None,
) -> None:
    block, reason = resolve_terminal_facts(migrated_conn, _EMPTY_SCOPE_HUB)
    assert block is None
    assert reason == TerminalFactsOmissionReason.burst_returned_no_claims.value


def test_ac3_no_terminal_claims(
    migrated_conn: sqlite3.Connection,
    non_terminal_hub_fixture: None,
) -> None:
    block, reason = resolve_terminal_facts(migrated_conn, _NON_TERMINAL_HUB)
    assert block is None
    assert reason == TerminalFactsOmissionReason.no_terminal_claims.value


def test_ac4_unreachable_reason_constant() -> None:
    assert (
        TerminalFactsOmissionReason.terminal_claims_outside_primary_vocabulary
        in TERMINAL_FACTS_UNREACHABLE_REASONS
    )
    assert len(TERMINAL_FACTS_UNREACHABLE_REASONS) == 1


def test_ac4_terminal_claims_outside_primary_vocabulary_under_monkeypatch(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _MORTGAGE_HUB, entity_type="case")
    _insert_assertion(
        migrated_conn,
        entity_id=_MORTGAGE_HUB,
        assertion_id=66003,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )
    terminal_item = BurstClaimItem(
        assertion_id=66003,
        claim=_A20701_CLAIM,
        predicate_form="denied(spread_extension, chase, 2026-06-26)",
        epistemic_state="confirmed",
        terminal=True,
        entity_id=_MORTGAGE_HUB,
        functor="denied",
        action="spread_extension",
        party="chase",
    )
    empty_vocab: frozenset[str] = frozenset()
    patched_domains = {**ACTION_VOCAB_BY_DOMAIN, "mortgage_escrow": empty_vocab}
    with patch(
        "cortex_store.terminal_facts.ACTION_VOCAB_BY_DOMAIN",
        patched_domains,
    ), patch(
        "cortex_store.terminal_facts.burst_claims",
        return_value=_burst_response([terminal_item]),
    ):
        block, reason = resolve_terminal_facts(migrated_conn, _MORTGAGE_HUB)
    assert block is None
    assert reason is None


def test_ac4_outside_vocabulary_reason_reachable_with_nonempty_vocab(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _MORTGAGE_HUB, entity_type="case")
    terminal_item = BurstClaimItem(
        assertion_id=66004,
        claim="appeal denied",
        predicate_form="denied(assessment_appeal_application, scc_aab, 2026-06-05)",
        epistemic_state="confirmed",
        terminal=True,
        entity_id=_MORTGAGE_HUB,
        functor="denied",
        action="assessment_appeal_application",
        party="scc_aab",
    )
    with patch(
        "cortex_store.terminal_facts.burst_claims",
        return_value=_burst_response([terminal_item]),
    ):
        block, reason = resolve_terminal_facts(migrated_conn, _MORTGAGE_HUB)
    assert block is None
    assert (
        reason
        == TerminalFactsOmissionReason.terminal_claims_outside_primary_vocabulary.value
    )


@pytest.mark.parametrize(
    ("entity_id", "expected_reason"),
    [
        (_UNRECOGNIZED_HUB, TerminalFactsOmissionReason.hub_domain_unrecognized.value),
        (_AAB_HUB, None),
        (_EMPTY_SCOPE_HUB, TerminalFactsOmissionReason.burst_returned_no_claims.value),
        (_NON_TERMINAL_HUB, TerminalFactsOmissionReason.no_terminal_claims.value),
    ],
    ids=[
        "hub_domain_unrecognized",
        "empty_vocab_carve_out",
        "burst_returned_no_claims",
        "no_terminal_claims",
    ],
)
def test_ac1_hub_none_block_implies_reason_except_empty_vocab(
    migrated_conn: sqlite3.Connection,
    aab_mortgage_bridge_fixture: None,
    empty_scope_hub_fixture: None,
    non_terminal_hub_fixture: None,
    entity_id: str,
    expected_reason: str | None,
) -> None:
    if entity_id == _UNRECOGNIZED_HUB:
        _seed_entity(migrated_conn, _UNRECOGNIZED_HUB, entity_type="case")
    if entity_id == _AAB_HUB:
        empty_tax = frozenset()
        patched_domains = {**ACTION_VOCAB_BY_DOMAIN, "tax_appeal": empty_tax}
        with patch(
            "cortex_store.terminal_facts.ACTION_VOCAB_BY_DOMAIN",
            patched_domains,
        ):
            block, reason = resolve_terminal_facts(migrated_conn, entity_id)
        assert block is None
        assert reason == expected_reason
        return
    block, reason = resolve_terminal_facts(migrated_conn, entity_id)
    assert block is None
    assert reason == expected_reason


def test_ac6_chase_hub_still_returns_terminal_facts_block(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _MORTGAGE_HUB, entity_type="case")
    _insert_assertion(
        migrated_conn,
        entity_id=_MORTGAGE_HUB,
        assertion_id=66005,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
    )
    resp = cortex_client.get(f"/entities/{_MORTGAGE_HUB}")
    assert resp.status_code == 200
    body = resp.json()
    assert "terminal_facts" in body
    assert "terminal_facts_omitted_reason" not in body
    assert body["terminal_facts"]["fact_count"] >= 1
