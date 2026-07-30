"""Dry-run materialization review surface — arc 6386 Fable dd1858ae §3–§5 tiering."""

from __future__ import annotations

import sqlite3

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.materialize_review import (
    TIER_AUTO,
    TIER_REVIEW,
    review_materialization,
    scan_matter_rows,
)

_ACCOUNT = "account:chase-mortgage-8787"
_CASE = "case:chase-escrow-flintridge-2026"
_APPEAL = "case:boe19p-flintridge-appeal-2026"
_OUT_OF_SCOPE = "todo:unrelated-materialize-fixture"

_DENIAL_CLAIM = (
    "WO #953902037 — the request to extend escrow shortage spread beyond the "
    "standard 12-month RESPA floor was DENIED on the 2026-04-29 callback."
)
_PENDING_CLAIM = (
    "WO #953902037 opened 2026-07-15 — spread extension request pending review "
    "with Chase Escalations."
)
_AMBIGUOUS_DATE_CLAIM = (
    "Escrow analysis pending: the 2026-05-01 statement was superseded by the "
    "2026-06-12 recalculation."
)
_APPEAL_CLAIM = (
    "Escrow analysis requested from the assessor on 2026-03-04 for the appeal record."
)


def _seed_entity(conn: sqlite3.Connection, entity_id: str, entity_type: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, entity_type, entity_id),
    )


def _insert(
    conn: sqlite3.Connection,
    *,
    assertion_id: int,
    entity_id: str,
    claim: str,
    valid_from: str | None = None,
    predicate_form: str | None = None,
    superseded_by: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "valid_from, review_status, predicate_form, superseded_by) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, '2026-07-30T00:00:00Z', "
        "?, 'committed', ?, ?)",
        (
            assertion_id,
            entity_id,
            claim,
            compute_claim_hash(entity_id, claim),
            valid_from,
            predicate_form,
            superseded_by,
        ),
    )


def _seed(conn: sqlite3.Connection) -> None:
    for entity_id, entity_type in (
        (_ACCOUNT, "account"),
        (_CASE, "case"),
        (_APPEAL, "case"),
        (_OUT_OF_SCOPE, "todo"),
    ):
        _seed_entity(conn, entity_id, entity_type)
    _insert(conn, assertion_id=900001, entity_id=_CASE, claim=_DENIAL_CLAIM)
    _insert(
        conn,
        assertion_id=900002,
        entity_id=_ACCOUNT,
        claim=_PENDING_CLAIM,
        valid_from="2026-07-15",
    )
    _insert(conn, assertion_id=900003, entity_id=_APPEAL, claim=_APPEAL_CLAIM)
    _insert(conn, assertion_id=900004, entity_id=_ACCOUNT, claim=_AMBIGUOUS_DATE_CLAIM)
    _insert(conn, assertion_id=900005, entity_id=_OUT_OF_SCOPE, claim=_DENIAL_CLAIM)
    conn.commit()


def _by_id(review, assertion_id: int):
    for candidate in review.candidates:
        if candidate.assertion_id == assertion_id:
            return candidate
    return None


def test_scan_excludes_out_of_scope_and_superseded_rows(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed(migrated_conn)
    _insert(
        migrated_conn,
        assertion_id=900006,
        entity_id=_CASE,
        claim=_PENDING_CLAIM,
        superseded_by=900001,
    )
    migrated_conn.commit()

    scanned = {int(row["id"]) for row in scan_matter_rows(migrated_conn)}
    assert 900005 not in scanned
    assert 900006 not in scanned
    assert {900001, 900002, 900003, 900004} <= scanned


def test_dispositions_are_review_tier(migrated_conn: sqlite3.Connection) -> None:
    _seed(migrated_conn)
    review = review_materialization(migrated_conn)

    denial = _by_id(review, 900001)
    assert denial is not None
    assert denial.tier == TIER_REVIEW
    assert "disposition_requires_review" in denial.reasons


def test_appeal_scope_is_always_review_tier(migrated_conn: sqlite3.Connection) -> None:
    _seed(migrated_conn)
    review = review_materialization(migrated_conn)

    appeal = _by_id(review, 900003)
    assert appeal is not None
    assert appeal.tier == TIER_REVIEW
    assert "appeal_scope_requires_review" in appeal.reasons


def test_ambiguous_dates_block_auto_tier(migrated_conn: sqlite3.Connection) -> None:
    _seed(migrated_conn)
    review = review_materialization(migrated_conn)

    ambiguous = _by_id(review, 900004)
    if ambiguous is None:
        return
    assert ambiguous.tier == TIER_REVIEW
    assert "ambiguous_literal_dates" in ambiguous.reasons
    assert ambiguous.proposed_valid_from is None


def test_single_dated_process_row_is_auto_tier(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed(migrated_conn)
    review = review_materialization(migrated_conn)

    pending = _by_id(review, 900002)
    assert pending is not None
    assert pending.tier == TIER_AUTO
    assert pending.reasons == ()
    assert pending.proposed_valid_from == "2026-07-15"
    assert pending.functor == "pending"


def test_small_sweep_collapses_auto_tier_to_review(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed(migrated_conn)
    review = review_materialization(migrated_conn)

    pending = _by_id(review, 900002)
    assert pending is not None
    assert review.collapse_auto_to_review is True
    assert review.effective_tier(pending) == TIER_REVIEW


def test_already_materialized_rows_produce_no_candidate(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed(migrated_conn)
    review = review_materialization(migrated_conn)
    pending = _by_id(review, 900002)
    assert pending is not None

    migrated_conn.execute(
        "UPDATE assertions SET predicate_form = ? WHERE id = ?",
        (pending.proposed_predicate_form, 900002),
    )
    migrated_conn.commit()

    second = review_materialization(migrated_conn)
    assert _by_id(second, 900002) is None
    assert second.already_materialized_count >= 1


def test_conflicting_stored_terminal_forces_review(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed(migrated_conn)
    migrated_conn.execute(
        "UPDATE assertions SET predicate_form = ? WHERE id = ?",
        ("granted(spread_extension, chase, 2026-04-29)", 900001),
    )
    migrated_conn.commit()

    review = review_materialization(migrated_conn)
    pending = _by_id(review, 900002)
    assert pending is not None
    assert pending.tier == TIER_REVIEW
    assert any(r.startswith("conflicts_with_stored_terminal") for r in pending.reasons)


def test_review_never_writes(migrated_conn: sqlite3.Connection) -> None:
    _seed(migrated_conn)
    before = migrated_conn.execute(
        "SELECT id, predicate_form, valid_from FROM assertions ORDER BY id"
    ).fetchall()

    review_materialization(migrated_conn)

    after = migrated_conn.execute(
        "SELECT id, predicate_form, valid_from FROM assertions ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in before] == [tuple(row) for row in after]
