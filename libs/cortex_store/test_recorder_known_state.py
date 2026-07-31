"""Hermetic tests for recorder known-state gate."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.dispatch_ops.ops_assertions_write import _op_assert
from cortex_store.models import AssertionCreate
from cortex_store.near_dup import DEDUP_SIMILARITY_THRESHOLD
from cortex_store.recorder_known_state import (
    check_recorder_known_state,
    extract_anchors,
    score_same_meaning,
    should_apply_recorder_known_state,
)
from cortex_store.routes.assertions._create import _create_assertion_impl


def _seed_entity(conn: sqlite3.Connection, entity_id: str, entity_type: str = "person") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, entity_type, entity_id),
    )
    conn.commit()


def _insert_prior(
    conn: sqlite3.Connection,
    *,
    entity_id: str,
    claim: str,
    evidence: str = "fixture",
    review_status: str | None = None,
    evidence_uris: list[str] | None = None,
) -> int:
    claim_hash = compute_claim_hash(entity_id, claim)
    cur = conn.execute(
        "INSERT INTO assertions "
        "(entity_id, claim, confidence, evidence, claim_hash, review_status, evidence_uris) "
        "VALUES (?, ?, 'believed', ?, ?, ?, ?)",
        (
            entity_id,
            claim,
            evidence,
            claim_hash,
            review_status,
            __import__("json").dumps(evidence_uris) if evidence_uris else None,
        ),
    )
    assertion_id = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) VALUES (?, ?, ?)",
        (assertion_id, entity_id, claim),
    )
    conn.commit()
    return assertion_id


def _base_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "entity_id": "person:operator",
        "claim": "Event 2026-07-17#overnight-rideshare-micro-sleeps caused circadian disruption.",
        "confidence": "believed",
        "evidence": "operator stated via Recorder",
        "derivation_type": "user_statement",
        "reasoning_summary": "Recorder fixture.",
        "observed_at": "2026-07-17T08:00:00Z",
    }
    body.update(overrides)
    return body


@pytest.fixture()
def bind_conn_db(
    migrated_conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> sqlite3.Connection:
    from cortex_store import db

    db_path = migrated_conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr(db, "_CORTEX_DB", db_path)
    return migrated_conn


def test_extract_anchors_from_claim_and_uri() -> None:
    anchors = extract_anchors(
        [
            "Re 2026-07-17#overnight-rideshare-micro-sleeps the cause was lack of sleep.",
            "cortex://notes/journal/2026-07-17#overnight-rideshare-micro-sleeps.md",
        ]
    )
    assert anchors == {"2026-07-17#overnight-rideshare-micro-sleeps"}


def test_should_apply_life_entity_not_service(migrated_conn: sqlite3.Connection) -> None:
    body = AssertionCreate.model_validate(_base_body(entity_id="service:api"))
    assert should_apply_recorder_known_state(body) is False
    life_body = AssertionCreate.model_validate(_base_body(entity_id="person:operator"))
    assert should_apply_recorder_known_state(life_body) is True


def test_same_anchor_redump_blocked_after_staged_correction(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, "person:operator")
    anchor = "2026-07-17#overnight-rideshare-micro-sleeps"
    prior_claim = (
        f"CORRECTION for {anchor}: circadian cause was overnight rideshare micro-sleeps."
    )
    prior_id = _insert_prior(
        migrated_conn,
        entity_id="person:operator",
        claim=prior_claim,
        review_status="staged",
    )
    redump = (
        f"Regarding {anchor}, circadian cause was overnight rideshare micro-sleeps."
    )
    body = AssertionCreate.model_validate(_base_body(claim=redump))
    result = check_recorder_known_state(migrated_conn, body)
    assert result.already_known is True
    assert result.matched_assertion_id == prior_id
    assert result.known_state_reason in {
        "same_anchor_high_lexical",
        "staged_sibling_collapse",
    }


def test_force_supersedes_always_allows_correction_write(
    bind_conn_db: sqlite3.Connection,
) -> None:
    conn = bind_conn_db
    _seed_entity(conn, "person:operator")
    anchor = "2026-07-17#overnight-rideshare-micro-sleeps"
    prior_id = _insert_prior(
        conn,
        entity_id="person:operator",
        claim=f"Prior about {anchor}: circadian cause was overnight rideshare micro-sleeps.",
        review_status="staged",
    )
    correction = (
        f"CORRECTION for {anchor}: circadian cause was overnight rideshare micro-sleeps."
    )
    result = _create_assertion_impl(
        _base_body(
            claim=correction,
            force=True,
            supersedes_id=prior_id,
        )
    )
    assert result["was_new"] is True
    assert result.get("already_known") is False
    count = conn.execute(
        "SELECT COUNT(*) FROM assertions WHERE entity_id = 'person:operator'"
    ).fetchone()[0]
    assert count == 2


def test_exact_claim_hash_returns_already_known(
    bind_conn_db: sqlite3.Connection,
) -> None:
    conn = bind_conn_db
    _seed_entity(conn, "person:operator")
    claim = "Exact duplicate claim for hash test."
    first = _create_assertion_impl(_base_body(claim=claim))
    assert first["was_new"] is True
    second = _create_assertion_impl(_base_body(claim=claim))
    assert second["was_new"] is False
    assert second["already_known"] is True
    assert second["known_state_reason"] == "exact_claim_hash"


def test_non_life_near_paraphrase_not_blocked(
    bind_conn_db: sqlite3.Connection,
) -> None:
    conn = bind_conn_db
    _seed_entity(conn, "service:stargate", entity_type="service")
    claim_a = "The gateway middleware applies caching for model metadata lookups."
    claim_b = "Gateway middleware applies caching for model metadata lookup operations."
    _insert_prior(conn, entity_id="service:stargate", claim=claim_a)
    result = _create_assertion_impl(
        {
            "entity_id": "service:stargate",
            "claim": claim_b,
            "confidence": "believed",
            "evidence": "cross-domain fixture",
            "derivation_type": "inference",
            "reasoning_summary": "scope test",
            "observed_at": "2026-07-17T08:00:00Z",
        }
    )
    assert result["was_new"] is True
    assert result.get("already_known") is not True


def test_reworded_same_anchor_below_threshold_passes_through(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, "person:operator")
    anchor = "2026-07-17#overnight-rideshare-micro-sleeps"
    prior = (
        f"{anchor}: operator slept poorly due to fragmented overnight transit naps."
    )
    _insert_prior(migrated_conn, entity_id="person:operator", claim=prior)
    reworded = (
        f"{anchor}: fatigue stemmed from disjointed vehicular rest intervals overnight."
    )
    score = score_same_meaning(prior, reworded)
    assert score < DEDUP_SIMILARITY_THRESHOLD
    body = AssertionCreate.model_validate(_base_body(claim=reworded))
    result = check_recorder_known_state(migrated_conn, body)
    assert result.already_known is False


def test_create_emits_recorder_already_known_event(
    bind_conn_db: sqlite3.Connection,
) -> None:
    conn = bind_conn_db
    _seed_entity(conn, "person:operator")
    anchor = "2026-07-17#overnight-rideshare-micro-sleeps"
    prior_claim = f"{anchor}: circadian cause was overnight rideshare micro-sleeps."
    _insert_prior(conn, entity_id="person:operator", claim=prior_claim)
    captured: list[str] = []

    def _capture(signal: str, **_: object) -> None:
        captured.append(signal)

    with patch("cortex_store.events_imprint.record", side_effect=_capture):
        result = _create_assertion_impl(_base_body(claim=prior_claim))
    assert result["already_known"] is True
    assert "graph.recorder.already_known" in captured


def test_mcp_assert_exposes_already_known_fields(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cortex_store import db
    from cortex_store.db import cortex_conn

    monkeypatch.setattr(db, "_CORTEX_DB", migrated_db_path)
    with cortex_conn() as conn:
        _seed_entity(conn, "person:operator")
        claim = "Shared claim for MCP already_known surface."
        _insert_prior(conn, entity_id="person:operator", claim=claim)
    result = _op_assert(
        entity_id="person:operator",
        claim=claim,
        confidence="believed",
        evidence="mcp test",
        derivation_type="user_statement",
    )
    assert result.get("already_known") is True
    assert result.get("matched_assertion_id")
    assert result.get("known_state_reason") == "exact_claim_hash"


def test_propose_already_known_emits_coordination_signal(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, "todo:ship")
    anchor = "2026-07-17#overnight-rideshare-micro-sleeps"
    _insert_prior(
        migrated_conn,
        entity_id="todo:ship",
        claim=f"{anchor}: blocked on review cycle.",
    )
    life_patch = {
        "@context": "cortex.life/v1",
        "@graph": [
            {
                "@id": "todo:ship",
                "noted": f"{anchor}: blocked on review cycle.",
            }
        ],
    }
    captured: list[str] = []

    def _capture(signal: str, **_: object) -> None:
        captured.append(signal)

    with patch("cortex_store.events_imprint.record", side_effect=_capture):
        resp = cortex_client.post("/graph/imprint/propose", json={"patch": life_patch})
    assert resp.status_code == 200
    body = resp.json()
    assert body["already_known"] is True
    assert body["proposal_id"] is None
    assert "graph.recorder.already_known" in captured
