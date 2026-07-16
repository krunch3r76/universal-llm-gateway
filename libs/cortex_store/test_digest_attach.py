"""Hermetic tests for digest attach resolve and dedup helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.conftest import bind_cortex_db
from cortex_store.digest_attach import (
    digest_attach_search_hits,
    digest_resolve_attach,
)
from cortex_store.digest_dedup import claim_hash_exists, fetch_semantic_dedup_candidates
from cortex_store.dispatch_ops.ops_digest import _build_claim_proposals


def _insert_entity(
    conn,
    *,
    entity_id: str,
    entity_type: str,
    name: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO entities (id, type, name, lifecycle, created_at, updated_at) "
        "VALUES (?, ?, ?, 'active', ?, ?)",
        (entity_id, entity_type, name, now, now),
    )


def _insert_assertion(
    conn,
    *,
    entity_id: str,
    claim: str,
    evidence_uris: list[str] | None = None,
    derivation_type: str = "user_statement",
    valid_from: str | None = None,
) -> int:
    now = datetime.now(UTC).isoformat()
    claim_hash = compute_claim_hash(entity_id, claim)
    cur = conn.execute(
        "INSERT INTO assertions "
        "(entity_id, claim, claim_hash, confidence, derivation_type, evidence_uris, "
        "valid_from, created_at) "
        "VALUES (?, ?, ?, 'believed', ?, ?, ?, ?)",
        (
            entity_id,
            claim,
            claim_hash,
            derivation_type,
            __import__("json").dumps(evidence_uris or []),
            valid_from,
            now,
        ),
    )
    assertion_id = int(cur.lastrowid or 0)
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) "
        "VALUES (?, ?, ?)",
        (assertion_id, entity_id, claim),
    )
    return assertion_id


@pytest.mark.offline
def test_digest_resolve_attach_unique_name_hit(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="finance:wf-ploc",
            entity_type="finance",
            name="Wells Fargo PLOC",
        )
        conn.commit()
        resolved, hits = digest_resolve_attach(conn, "finance:wf-ploc")
        search_hits = digest_attach_search_hits(conn, "Wells Fargo PLOC")

    assert resolved == "finance:wf-ploc"
    assert hits == []
    assert search_hits == ["finance:wf-ploc"]


@pytest.mark.offline
def test_digest_resolve_attach_ambiguous_names(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="finance:wf-ploc-a",
            entity_type="finance",
            name="Wells Fargo PLOC A",
        )
        _insert_entity(
            conn,
            entity_id="finance:wf-ploc-b",
            entity_type="finance",
            name="Wells Fargo PLOC B",
        )
        conn.commit()
        resolved, hits = digest_resolve_attach(conn, "Wells Fargo PLOC")

    assert resolved is None
    assert len(hits) >= 2


@pytest.mark.offline
def test_claim_hash_exists_on_resolved_entity(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    claim_text = "WF rep stated payment overdue"
    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="finance:wf-ploc",
            entity_type="finance",
            name="Wells Fargo PLOC",
        )
        assertion_id = _insert_assertion(
            conn, entity_id="finance:wf-ploc", claim=claim_text
        )
        conn.commit()
        existing = claim_hash_exists(conn, "finance:wf-ploc", claim_text)

    assert existing == assertion_id


@pytest.mark.offline
def test_find_existing_assertion_dedup_skips_staged_assertion(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    claim = {
        "claim": "WF rep stated payment overdue",
        "p_class": "P2",
        "canonicality": "assert",
        "attach_hint": "finance:wf-ploc",
        "flags": [],
        "evidence_anchor": "wells-fargo-ploc",
    }
    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="finance:wf-ploc",
            entity_type="finance",
            name="Wells Fargo PLOC",
        )
        existing_id = _insert_assertion(
            conn, entity_id="finance:wf-ploc", claim=claim["claim"]
        )
        conn.commit()
        proposals, skipped, _ = _build_claim_proposals(
            conn,
            claim=claim,
            claim_index=0,
            entry_anchor="2026-07-13#wells-fargo-ploc",
            journal_entity_id="document:journal-2026-07-13",
            journal_uri="cortex://notes/journal/2026-07-13.md",
        )

    assert skipped == [f"assertion:{existing_id}"]
    assert not any(p.proposal_type == "assertion" for p in proposals)


@pytest.mark.offline
def test_prose_canonicality_skips_assertion_and_entity_propose(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    claim = {
        "claim": "Operator infers strategy may shift next quarter",
        "p_class": "P3",
        "canonicality": "prose",
        "attach_hint": "finance:wf-ploc",
        "flags": [],
        "evidence_anchor": "strategy",
    }
    with cortex_conn() as conn:
        proposals, skipped, _ = _build_claim_proposals(
            conn,
            claim=claim,
            claim_index=2,
            entry_anchor="2026-07-13#strategy",
            journal_entity_id="document:journal-2026-07-13",
            journal_uri="cortex://notes/journal/2026-07-13.md",
        )

    assert skipped == ["skipped_prose:2"]
    assert proposals == []


@pytest.mark.offline
def test_tokenize_pge_ampersand_and_or_search(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn
    from cortex_store.digest_attach import _tokenize_hint

    assert "pge" in _tokenize_hint("PG&E, Marlena")

    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            entity_type="case",
            name="PG&E gas backbilling dispute",
        )
        conn.commit()
        resolved, hits = digest_resolve_attach(conn, "PG&E backbilling")

    assert resolved == "case:pge-gas-backbilling-dispute-2026"
    assert "case:pge-gas-backbilling-dispute-2026" in hits


@pytest.mark.offline
def test_deadline_claim_stages_deadline_for_relationship(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    claim = {
        "claim": "PG&E payment deadline is July 17, 2026",
        "p_class": "P1",
        "canonicality": "assert",
        "attach_hint": "utility:pge",
        "flags": [],
        "evidence_anchor": "pge-deadline",
    }
    with cortex_conn() as conn:
        proposals, skipped, _ = _build_claim_proposals(
            conn,
            claim=claim,
            claim_index=1,
            entry_anchor="2026-07-13#pge",
            journal_entity_id="document:journal-2026-07-13",
            journal_uri="cortex://notes/journal/2026-07-13.md",
        )

    rel_proposals = [p for p in proposals if p.proposal_type == "relationship"]
    assert len(rel_proposals) == 1
    assert rel_proposals[0].proposal_json["type_id"] == "deadline_for"
    assert rel_proposals[0].proposal_json["valid_from"] == "2026-07-17"
    assert "skipped_prose" not in "".join(skipped)


@pytest.mark.offline
def test_fts_candidate_join_uses_assertion_id_not_rowid(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    journal_uri = "cortex://notes/journal/2026-07-13.md"
    claim_text = "PG&E payment deadline is July 17, 2026 for $786.71"
    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            entity_type="case",
            name="PG&E gas backbilling dispute",
        )
        assertion_id = _insert_assertion(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            claim=claim_text,
            evidence_uris=[journal_uri],
            derivation_type="user_statement",
            valid_from="2026-07-17",
        )
        conn.commit()
        candidates = fetch_semantic_dedup_candidates(
            conn,
            claim_text=claim_text,
            candidate_entity_ids=["case:pge-gas-backbilling-dispute-2026"],
            journal_uri=journal_uri,
            expected_derivation_type="user_statement",
        )

    assert len(candidates) == 1
    assert candidates[0]["id"] == assertion_id


@pytest.mark.offline
def test_semantic_duplicate_of_skips_assertion_and_deferred_entity(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn
    from cortex_store.digest_dedup import compute_dedup_candidate_fingerprint

    journal_uri = "cortex://notes/journal/2026-07-13.md"
    claim_text = "PG&E gas backbilling dispute balance is $786.71"
    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            entity_type="case",
            name="PG&E gas backbilling dispute",
        )
        existing_id = _insert_assertion(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            claim=claim_text,
            evidence_uris=[journal_uri],
            derivation_type="user_statement",
        )
        conn.commit()
        fingerprint = compute_dedup_candidate_fingerprint(
            assertion_id=existing_id,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            claim=claim_text,
            derivation_type="user_statement",
            evidence_uris=[journal_uri],
            valid_from=None,
            valid_until=None,
        )
        claim = {
            "claim": claim_text,
            "p_class": "P1",
            "canonicality": "assert",
            "attach_hint": "utility:pge-new-dispute",
            "flags": [],
            "evidence_anchor": "pge-balance",
            "verify_verdict": "pass",
            "duplicate_of": existing_id,
            "dedup_candidate_fingerprint": fingerprint,
        }
        proposals, skipped, _ = _build_claim_proposals(
            conn,
            claim=claim,
            claim_index=0,
            entry_anchor="2026-07-13#pge",
            journal_entity_id="document:journal-2026-07-13",
            journal_uri=journal_uri,
        )

    assert skipped == [f"assertion:{existing_id}"]
    assert not any(p.proposal_type == "assertion" for p in proposals)
    assert not any(p.proposal_type == "entity" for p in proposals)


@pytest.mark.offline
def test_numeric_conflict_does_not_semantic_skip(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn

    journal_uri = "cortex://notes/journal/2026-07-13.md"
    existing_claim = "PG&E payment deadline is July 17, 2026 for $786.71"
    new_claim = "PG&E payment deadline is July 17, 2026 for $495.18"
    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            entity_type="case",
            name="PG&E gas backbilling dispute",
        )
        _insert_assertion(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            claim=existing_claim,
            evidence_uris=[journal_uri],
            derivation_type="user_statement",
            valid_from="2026-07-17",
        )
        conn.commit()
        claim = {
            "claim": new_claim,
            "p_class": "P1",
            "canonicality": "assert",
            "attach_hint": "case:pge-gas-backbilling-dispute-2026",
            "flags": [],
            "evidence_anchor": "pge-deadline",
            "verify_verdict": "pass",
        }
        proposals, skipped, _ = _build_claim_proposals(
            conn,
            claim=claim,
            claim_index=0,
            entry_anchor="2026-07-13#pge",
            journal_entity_id="document:journal-2026-07-13",
            journal_uri=journal_uri,
        )

    assert skipped == []
    assert any(p.proposal_type == "assertion" for p in proposals)


@pytest.mark.offline
def test_temporal_mismatch_rejects_semantic_skip(
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_cortex_db(monkeypatch, migrated_db_path)
    from cortex_store.db import cortex_conn
    from cortex_store.digest_dedup import compute_dedup_candidate_fingerprint

    journal_uri = "cortex://notes/journal/2026-07-13.md"
    claim_text = "PG&E gas backbilling dispute balance is $786.71"
    with cortex_conn() as conn:
        _insert_entity(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            entity_type="case",
            name="PG&E gas backbilling dispute",
        )
        existing_id = _insert_assertion(
            conn,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            claim=claim_text,
            evidence_uris=[journal_uri],
            derivation_type="user_statement",
            valid_from="2026-07-17",
        )
        conn.commit()
        fingerprint = compute_dedup_candidate_fingerprint(
            assertion_id=existing_id,
            entity_id="case:pge-gas-backbilling-dispute-2026",
            claim=claim_text,
            derivation_type="user_statement",
            evidence_uris=[journal_uri],
            valid_from="2026-07-17",
            valid_until=None,
        )
        claim = {
            "claim": "PG&E gas backbilling dispute balance remains $786.71",
            "p_class": "P1",
            "canonicality": "assert",
            "attach_hint": "case:pge-gas-backbilling-dispute-2026",
            "flags": [],
            "evidence_anchor": "pge-balance",
            "valid_from_hint": "2026-07-18",
            "verify_verdict": "pass",
            "duplicate_of": existing_id,
            "dedup_candidate_fingerprint": fingerprint,
        }
        proposals, skipped, _ = _build_claim_proposals(
            conn,
            claim=claim,
            claim_index=0,
            entry_anchor="2026-07-13#pge",
            journal_entity_id="document:journal-2026-07-13",
            journal_uri=journal_uri,
        )

    assert skipped == []
    assert any(p.proposal_type == "assertion" for p in proposals)
