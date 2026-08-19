"""Unit tests for build_recall_card — nulls, continuity-not-burst, bare resolve."""

from __future__ import annotations

import sqlite3

import pytest

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.recall_card import build_recall_card
from cortex_store.recall_models import RecallNull
from cortex_store.recall_resolve import resolve_recall_inputs

_HUB = "case:test-escrow-hub"
_DENIAL_CLAIM = (
    "Chase escrow shortage spread extension request was DENIED on 2026-04-29. "
    "Nell stated we are unable to spread the escrow shortage over 12 months."
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
) -> None:
    claim_hash = compute_claim_hash(entity_id, claim)
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "review_status, entrenchment_score) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, '2026-04-29T00:00:00Z', "
        "'committed', 0.8)",
        (assertion_id, entity_id, claim, claim_hash),
    )
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) VALUES (?, ?, ?)",
        (assertion_id, entity_id, claim),
    )
    conn.commit()


@pytest.fixture()
def escrow_hub(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _HUB, entity_type="case")
    _insert_assertion(
        migrated_conn,
        entity_id=_HUB,
        assertion_id=99001,
        claim=_DENIAL_CLAIM,
    )


def test_matter_card_resolves_seeded_hub(migrated_conn: sqlite3.Connection, escrow_hub) -> None:
    card = build_recall_card(
        migrated_conn,
        mode="matter",
        q=None,
        seeds=[_HUB],
    )
    assert card.mode == "matter"
    assert [r.entity_id for r in card.resolved] == [_HUB]
    assert card.dispositions
    assert RecallNull.resolver_miss not in card.nulls


def test_continuity_card_has_empty_dispositions(
    migrated_conn: sqlite3.Connection,
    migrated_db_path,
    monkeypatch: pytest.MonkeyPatch,
    escrow_hub,
) -> None:
    from cortex_store import db

    monkeypatch.setattr(db, "_CORTEX_DB", migrated_db_path)
    card = build_recall_card(
        migrated_conn,
        mode="continuity",
        q=None,
        seeds=[_HUB],
    )
    assert card.dispositions == []
    assert RecallNull.vocab_not_covered not in card.nulls


def test_bare_ref_unique_resolve(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, "todo:unique-recall-target", entity_type="todo")
    migrated_conn.execute(
        "INSERT INTO surface_forms (entity_id, mention) VALUES (?, ?)",
        ("todo:unique-recall-target", "UniqueRecallMention"),
    )
    migrated_conn.commit()

    outcome = resolve_recall_inputs(migrated_conn, q="UniqueRecallMention", seeds=None)
    assert len(outcome.resolved) == 1
    assert outcome.resolved[0].entity_id == "todo:unique-recall-target"


def test_resolver_miss_on_unknown_seed(migrated_conn: sqlite3.Connection) -> None:
    card = build_recall_card(
        migrated_conn,
        mode="matter",
        q=None,
        seeds=["case:does-not-exist-recall"],
    )
    assert RecallNull.resolver_miss in card.nulls


def test_search_seeder_reads_pydantic_summary_items(monkeypatch: pytest.MonkeyPatch) -> None:
    from cortex_store import recall_resolve as rr
    from cortex_store.models.search import (
        AssertionSearchResult,
        AssertionSearchSummaryItem,
    )

    payload = AssertionSearchResult(
        query="tax appeal",
        intent="summary",
        items=[
            AssertionSearchSummaryItem(
                id=1,
                entity_id="case:seed-from-search",
                claim="tax appeal filing",
                confidence="confirmed",
            )
        ],
        total=1,
        search_mode="fulltext",
    )
    monkeypatch.setattr(rr, "_search_assertions_impl", lambda **_kwargs: payload)
    ids = rr._search_seed_entity_ids(None, "tax appeal")  # type: ignore[arg-type]
    assert ids == ["case:seed-from-search"]
