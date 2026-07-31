"""Hermetic tests for near-duplicate candidate selection."""

from __future__ import annotations

import sqlite3

import pytest

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.enrichment import reindex_assertion_fts_conn
from cortex_store.entity_crud import create_entity_impl
from cortex_store.near_dup import check_near_duplicate

_CLOSED_CLAIM = (
    "The gateway routes inference requests through stargate federation hops daily"
)
_OPEN_CLAIM = (
    "The gateway routes inference requests through stargate federation hops nightly"
)
_NEW_CLAIM = (
    "The gateway routes inference requests through stargate federation hops daily now"
)


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    return migrated_conn


def _seed_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    create_entity_impl(
        conn,
        {
            "id": entity_id,
            "type": entity_id.split(":", 1)[0],
            "name": entity_id.split(":", 1)[-1],
        },
    )


def _insert_assertion(conn: sqlite3.Connection, entity_id: str, claim: str) -> int:
    claim_hash = compute_claim_hash(entity_id, claim)
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, evidence, "
        "derivation_type, claim_hash, entrenchment_score) "
        "VALUES (?, ?, 'believed', 'ev', 'inference', ?, 0.1)",
        (entity_id, claim, claim_hash),
    )
    return int(cur.lastrowid)


@pytest.mark.offline
def test_closed_assertion_excluded_from_near_dup_candidates(
    conn: sqlite3.Connection,
) -> None:
    entity_id = "project:near-dup-closed"
    _seed_entity(conn, entity_id)
    closed_id = _insert_assertion(conn, entity_id, _CLOSED_CLAIM)
    conn.execute(
        "UPDATE assertions SET valid_until = '2026-06-10T12:00:00Z' WHERE id = ?",
        (closed_id,),
    )
    reindex_assertion_fts_conn(conn, closed_id)
    conn.commit()

    match = check_near_duplicate(conn, entity_id, _NEW_CLAIM, new_assertion_id=9999)

    assert match is None


@pytest.mark.offline
def test_open_assertion_still_eligible_near_dup_candidate(
    conn: sqlite3.Connection,
) -> None:
    entity_id = "project:near-dup-open"
    _seed_entity(conn, entity_id)
    closed_id = _insert_assertion(conn, entity_id, _CLOSED_CLAIM)
    conn.execute(
        "UPDATE assertions SET valid_until = '2026-06-10T12:00:00Z' WHERE id = ?",
        (closed_id,),
    )
    open_id = _insert_assertion(conn, entity_id, _OPEN_CLAIM)
    reindex_assertion_fts_conn(conn, closed_id)
    reindex_assertion_fts_conn(conn, open_id)
    conn.commit()

    match = check_near_duplicate(conn, entity_id, _NEW_CLAIM, new_assertion_id=9999)

    assert match is not None
    assert match.existing_id == open_id
    assert match.score >= 0.85
