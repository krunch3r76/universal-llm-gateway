"""Claims burst disclosure contract — arc 6386 Q3 acceptance tests."""

from __future__ import annotations

import inspect
import json
import sqlite3
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from predicate_form.action_detection import match_claim_segments
from predicate_form.action_enrichment import (
    dry_run_enrich_assertions,
    enrich_action_predicate_from_claim,
    enrich_action_predicate_from_claim_with_reason,
)

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.models.claims_burst import (
    BURST_ANOMALY_DROP_REASONS,
    BURST_DROP_ID_CAP,
)

_ENTITY = "account:chase-mortgage-8787"

_A20701_CLAIM = (
    "WO 956908029 / lower-payment request — DENIED, confirmed 2026-06-26. "
    "On the 2026-06-26 ~12:30 PM Chase Escalations call (case ECW260413-02188, "
    "rep 'Matthew', who reviewed Janet's notes), Chase stated it is unable to "
    "spread the escrow shortage beyond 12 months and that the request for the "
    "lower payment was DENIED."
)

_AMBIGUOUS_SPREAD_CLAIM = (
    "Spread extension was DENIED on 2026-03-01 per servicer policy. "
    "Separate update: spread extension was granted on 2026-04-01 after appeal."
)

_NO_MATCH_CLAIM = "Customer called about account balance and payment due date."

_SPREAD_DENIAL_CLAIM = (
    "WO #953902037 — Kaywan's request to extend escrow shortage spread beyond "
    "the standard 12-month RESPA floor — was DENIED on the 2026-04-29 Nell Cruz "
    "callback. Nell stated: 'we are unable to spread the escrow shortage over "
    "12 months.'"
)


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
    entity_id: str = _ENTITY,
    observed_at: str = "2026-07-30T00:00:00Z",
    review_status: str = "committed",
    predicate_form: str = "status(account:chase-mortgage-8787, denied, current)",
) -> None:
    claim_hash = compute_claim_hash(entity_id, claim)
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "review_status, predicate_form) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, ?, ?, ?)",
        (
            assertion_id,
            entity_id,
            claim,
            claim_hash,
            observed_at,
            review_status,
            predicate_form,
        ),
    )
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) VALUES (?, ?, ?)",
        (assertion_id, entity_id, claim),
    )
    conn.commit()


def _burst_payload(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "vocabulary": ["spread_extension"],
        "scope_entity_ids": [_ENTITY],
        "include_contradictions": False,
    }
    payload.update(overrides)
    return payload


def _drop_group(body: dict[str, Any], reason: str) -> dict[str, Any] | None:
    for group in body["disclosure"]["drops"]:
        if group["reason"] == reason:
            return group
    return None


def _assert_accounting(body: dict[str, Any]) -> None:
    disclosure = body["disclosure"]
    assert disclosure["rows_scanned"] == disclosure["rows_returned"] + disclosure["rows_dropped_total"]
    assert disclosure["rows_dropped_total"] == sum(group["count"] for group in disclosure["drops"])


@pytest.fixture()
def escrow_fixture(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _ENTITY)
    _insert_assertion(
        migrated_conn,
        assertion_id=20701,
        claim=_A20701_CLAIM,
        observed_at="2026-06-26T19:54:57Z",
    )
    _insert_assertion(
        migrated_conn,
        assertion_id=7738,
        claim=_SPREAD_DENIAL_CLAIM,
        observed_at="2026-04-29T17:10:00Z",
        review_status="staged",
    )
    _insert_assertion(
        migrated_conn,
        assertion_id=99001,
        claim=(
            "WO #953902037 opened 2026-07-15 — spread extension request pending review "
            "with Chase Escalations."
        ),
        observed_at="2026-07-15T10:00:00Z",
        review_status="staged",
        predicate_form="status(account:chase-mortgage-8787, pending, current)",
    )


@pytest.fixture()
def mixed_drop_fixture(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _ENTITY)
    _insert_assertion(
        migrated_conn,
        assertion_id=50001,
        claim=_AMBIGUOUS_SPREAD_CLAIM,
        observed_at="2026-04-01T00:00:00Z",
    )
    _insert_assertion(
        migrated_conn,
        assertion_id=50002,
        claim=_NO_MATCH_CLAIM,
        observed_at="2026-04-02T00:00:00Z",
    )
    _insert_assertion(
        migrated_conn,
        assertion_id=50003,
        claim=_SPREAD_DENIAL_CLAIM,
        observed_at="2026-04-03T00:00:00Z",
    )


def test_ac1_disclosure_accounting_identity(
    cortex_client: TestClient,
    mixed_drop_fixture: None,
) -> None:
    resp = cortex_client.post(
        "/claims/burst",
        json=_burst_payload(vocabulary=["payment_reduction"]),
    )
    assert resp.status_code == 200
    body = resp.json()
    _assert_accounting(body)
    reasons = {group["reason"] for group in body["disclosure"]["drops"]}
    assert "detector_declined_ambiguous" in reasons
    assert "detector_no_match" in reasons
    assert "action_out_of_vocabulary" in reasons


def test_ac2_ambiguous_spread_extension_named_separately(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _ENTITY)
    _insert_assertion(
        migrated_conn,
        assertion_id=60001,
        claim=_AMBIGUOUS_SPREAD_CLAIM,
        observed_at="2026-04-01T00:00:00Z",
    )
    resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    body = resp.json()
    assert body["claims"] == []
    ambiguous = _drop_group(body, "detector_declined_ambiguous")
    assert ambiguous is not None
    assert 60001 in ambiguous["assertion_ids"]
    no_match = _drop_group(body, "detector_no_match")
    assert no_match is None or 60001 not in no_match["assertion_ids"]


def test_ac4_openapi_disclosure_field_additive(
    cortex_client: TestClient,
) -> None:
    schema = cortex_client.get("/openapi.json").json()
    response_schema = schema["components"]["schemas"]["ClaimsBurstResponse"]
    properties = response_schema["properties"]
    assert "disclosure" in properties
    assert properties["disclosure"]["$ref"].endswith("BurstDisclosure")
    for field in ("vocabulary", "scope_entity_ids", "mode", "claims", "contradiction_pairs"):
        assert field in properties
        assert properties[field]["type"] in {"array", "string"} or field == "mode"


def test_ac5_no_anomaly_groups_on_escrow_fixture(
    cortex_client: TestClient,
    escrow_fixture: None,
) -> None:
    resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    body = resp.json()
    anomaly_values = {reason.value for reason in BURST_ANOMALY_DROP_REASONS}
    for group in body["disclosure"]["drops"]:
        assert group["reason"] not in anomaly_values
    assert body["disclosure"]["vocabulary_rejected"] == []


def test_ac6_disclosure_carries_ids_only(
    cortex_client: TestClient,
    escrow_fixture: None,
) -> None:
    resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    body = resp.json()
    disclosure_json = json.dumps(body["disclosure"])
    for claim_text in (_A20701_CLAIM, _SPREAD_DENIAL_CLAIM):
        for start in range(0, min(len(claim_text), 40)):
            snippet = claim_text[start : start + 20].strip()
            if len(snippet) >= 8:
                assert snippet not in disclosure_json
    for group in body["disclosure"]["drops"]:
        for assertion_id in group["assertion_ids"]:
            assert isinstance(assertion_id, int)


def test_ac7_drop_id_cap_disclosed(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _ENTITY)
    over_cap = BURST_DROP_ID_CAP + 1
    for offset, assertion_id in enumerate(range(70001, 70001 + over_cap)):
        _insert_assertion(
            migrated_conn,
            assertion_id=assertion_id,
            claim=f"{_NO_MATCH_CLAIM} Reference token {offset}.",
            observed_at="2026-01-01T00:00:00Z",
        )
    resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    body = resp.json()
    group = _drop_group(body, "detector_no_match")
    assert group is not None
    assert group["count"] == over_cap
    assert len(group["assertion_ids"]) == BURST_DROP_ID_CAP
    assert group["assertion_ids_truncated"] is True


def test_ac7_under_cap_not_truncated(
    cortex_client: TestClient,
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _ENTITY)
    _insert_assertion(
        migrated_conn,
        assertion_id=80001,
        claim=_NO_MATCH_CLAIM,
        observed_at="2026-01-01T00:00:00Z",
    )
    resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    group = _drop_group(resp.json(), "detector_no_match")
    assert group is not None
    assert group["count"] == 1
    assert group["assertion_ids_truncated"] is False


def test_ac8_burst_disclosure_path_is_read_only(
    cortex_client: TestClient,
    escrow_fixture: None,
    migrated_conn: sqlite3.Connection,
) -> None:
    before = migrated_conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = 20701"
    ).fetchone()[0]
    update_calls: list[dict[str, object]] = []

    def _track_update(**kwargs: object) -> dict[str, str]:
        update_calls.append(dict(kwargs))
        return {"error": "blocked in test"}

    with patch(
        "cortex_store.routes.assertions._update._update_assertion_impl",
        side_effect=_track_update,
    ):
        resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    assert resp.json()["disclosure"]["rows_scanned"] >= 1
    assert update_calls == []
    after = migrated_conn.execute(
        "SELECT predicate_form FROM assertions WHERE id = 20701"
    ).fetchone()[0]
    assert after == before


def test_ac9_delegators_and_dry_run_untouched() -> None:
    match_sig = inspect.signature(match_claim_segments)
    assert match_sig.return_annotation in (inspect._empty, "SegmentMatch | None") or (
        "SegmentMatch" in str(match_sig.return_annotation)
    )
    enrich_sig = inspect.signature(enrich_action_predicate_from_claim)
    assert "EnrichmentPreview" in str(enrich_sig.return_annotation)
    dry_run_source = inspect.getsource(dry_run_enrich_assertions)
    assert "enrich_action_predicate_from_claim(" in dry_run_source
    assert "with_reason" not in dry_run_source.replace(
        "enrich_action_predicate_from_claim(", ""
    )


def test_ac9_with_reason_delegators_match_public_signatures() -> None:
    preview, reason = enrich_action_predicate_from_claim_with_reason(
        _SPREAD_DENIAL_CLAIM,
        _ENTITY,
        assertion_id=7738,
    )
    assert preview is not None
    assert reason is None
    assert enrich_action_predicate_from_claim(
        _SPREAD_DENIAL_CLAIM,
        _ENTITY,
        assertion_id=7738,
    ) == preview


def test_escrow_fixture_disclosure_present(
    cortex_client: TestClient,
    escrow_fixture: None,
) -> None:
    resp = cortex_client.post("/claims/burst", json=_burst_payload())
    assert resp.status_code == 200
    body = resp.json()
    disclosure = body["disclosure"]
    assert disclosure["disclosure_version"] == 1
    assert disclosure["detector_version"] == "action_enrichment_template_v0"
    assert disclosure["vocabulary_requested"] == ["spread_extension"]
    assert disclosure["vocabulary_accepted"] == ["spread_extension"]
    _assert_accounting(body)
