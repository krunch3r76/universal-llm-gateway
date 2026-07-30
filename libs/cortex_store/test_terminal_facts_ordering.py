"""Arc 6386 §7 — terminal_facts band ordering and cap presentation tests."""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

from cortex_store.claim_hash import compute_claim_hash
from cortex_store.models.claims_burst import BurstClaimItem
from cortex_store.terminal_facts import (
    _partition_terminal_rows,
    resolve_terminal_facts,
)

_HUB = "case:ordering-fixture-6386-7"
_DENIAL_CLAIM = (
    "WO spread extension request DENIED on {date} — Chase unable to spread "
    "escrow shortage beyond 12 months."
)


def _burst_item(
    *,
    assertion_id: int,
    disposition_date: str | None,
    hop_distance: int = 0,
    entity_id: str = _HUB,
) -> BurstClaimItem:
    if disposition_date:
        predicate_form = f"denied(spread_extension, chase, {disposition_date})"
    else:
        predicate_form = "denied(spread_extension, chase)"
    return BurstClaimItem(
        assertion_id=assertion_id,
        claim=_DENIAL_CLAIM.format(date=disposition_date or "unknown"),
        predicate_form=predicate_form,
        epistemic_state=None,
        terminal=True,
        entity_id=entity_id,
        functor="denied",
        action="spread_extension",
        party="chase",
        hop_distance=hop_distance,
        arrival_path=[_HUB],
    )


def test_equal_disposition_date_retains_proximity_ascending_in_dated_band() -> None:
    same_date = "2026-06-26"
    rows = [
        _burst_item(assertion_id=300, disposition_date=same_date, hop_distance=2),
        _burst_item(assertion_id=100, disposition_date=same_date, hop_distance=0),
        _burst_item(assertion_id=200, disposition_date=same_date, hop_distance=1),
    ]
    ordered = _partition_terminal_rows(rows, hub_entity_id=_HUB)
    assert [item.assertion_id for item in ordered[:3]] == [100, 200, 300]


def test_dated_and_undated_bands_share_proximity_ascending_tiebreak() -> None:
    same_date = "2026-06-26"
    dated_rows = [
        _burst_item(assertion_id=10, disposition_date=same_date, hop_distance=1),
        _burst_item(assertion_id=20, disposition_date=same_date, hop_distance=0),
    ]
    undated_rows = [
        _burst_item(assertion_id=30, disposition_date=None, hop_distance=2),
        _burst_item(assertion_id=40, disposition_date=None, hop_distance=0),
    ]
    ordered = _partition_terminal_rows(dated_rows + undated_rows, hub_entity_id=_HUB)
    assert [item.assertion_id for item in ordered] == [20, 10, 40, 30]


def _seed_entity(conn: sqlite3.Connection, entity_id: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (entity_id, "case", entity_id),
    )
    conn.commit()


def _insert_denial(
    conn: sqlite3.Connection,
    *,
    assertion_id: int,
    valid_from: str | None,
    hop_entity: str = _HUB,
) -> None:
    if valid_from:
        claim = _DENIAL_CLAIM.format(date=valid_from)
        predicate_form = f"denied(spread_extension, chase, {valid_from})"
    else:
        claim = f"{_DENIAL_CLAIM.format(date='unknown')} assertion {assertion_id}"
        predicate_form = "denied(spread_extension, chase)"
    claim_hash = compute_claim_hash(hop_entity, claim)
    conn.execute(
        "INSERT INTO assertions "
        "(id, entity_id, claim, confidence, evidence, claim_hash, observed_at, "
        "valid_from, review_status, predicate_form) "
        "VALUES (?, ?, ?, 'confirmed', 'fixture', ?, '2026-07-30T00:00:00Z', ?, "
        "'committed', ?)",
        (
            assertion_id,
            hop_entity,
            claim,
            claim_hash,
            valid_from,
            predicate_form,
        ),
    )
    conn.execute(
        "INSERT INTO assertions_fts (assertion_id, entity_id, indexed_text) VALUES (?, ?, ?)",
        (assertion_id, hop_entity, claim),
    )
    conn.commit()


@pytest.fixture()
def cap_pressure_fixture(migrated_conn: sqlite3.Connection) -> None:
    _seed_entity(migrated_conn, _HUB)
    for idx in range(20):
        day = f"{idx + 1:02d}"
        _insert_denial(
            migrated_conn,
            assertion_id=70000 + idx,
            valid_from=f"2026-01-{day}",
        )


def test_cap_keeps_newest_dated_facts_and_counts_older_dropped(
    migrated_conn: sqlite3.Connection,
    cap_pressure_fixture: None,
) -> None:
    with patch("cortex_store.terminal_facts.TERMINAL_FACTS_CAP", 5):
        block, omitted = resolve_terminal_facts(migrated_conn, _HUB)
    assert omitted is None
    assert block is not None
    assert block.capped is True
    assert block.fact_count == 20
    assert block.facts_dropped == 15
    assert len(block.facts) == 5
    retained_dates = [fact.disposition_date for fact in block.facts if not fact.undated]
    assert retained_dates == [
        "2026-01-16",
        "2026-01-17",
        "2026-01-18",
        "2026-01-19",
        "2026-01-20",
    ]


def test_retained_dated_facts_presented_oldest_first(
    migrated_conn: sqlite3.Connection,
    cap_pressure_fixture: None,
) -> None:
    with patch("cortex_store.terminal_facts.TERMINAL_FACTS_CAP", 5):
        block, _ = resolve_terminal_facts(migrated_conn, _HUB)
    assert block is not None
    dates = [fact.disposition_date for fact in block.facts if not fact.undated]
    assert dates == sorted(dates)


def test_undated_band_stays_trailing_after_dated_presentation_reversal(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _HUB)
    _insert_denial(migrated_conn, assertion_id=81001, valid_from="2026-05-01")
    _insert_denial(migrated_conn, assertion_id=81002, valid_from="2026-06-01")
    _insert_denial(migrated_conn, assertion_id=81003, valid_from=None, hop_entity=_HUB)
    _insert_denial(
        migrated_conn,
        assertion_id=81004,
        valid_from=None,
        hop_entity=_HUB,
    )
    block, _ = resolve_terminal_facts(migrated_conn, _HUB)
    assert block is not None
    ids = [fact.assertion_id for fact in block.facts]
    assert ids.index(81001) < ids.index(81002)
    undated_ids = [fact.assertion_id for fact in block.facts if fact.undated]
    assert undated_ids == [81003, 81004]
    first_undated_idx = next(
        idx for idx, fact in enumerate(block.facts) if fact.undated
    )
    last_dated_idx = max(
        idx for idx, fact in enumerate(block.facts) if not fact.undated
    )
    assert first_undated_idx > last_dated_idx


def test_cap_with_mixed_bands_reverses_only_dated_prefix(
    migrated_conn: sqlite3.Connection,
) -> None:
    _seed_entity(migrated_conn, _HUB)
    for idx in range(3):
        day = f"{idx + 1:02d}"
        _insert_denial(
            migrated_conn,
            assertion_id=82000 + idx,
            valid_from=f"2026-06-{day}",
        )
    _insert_denial(migrated_conn, assertion_id=82010, valid_from=None)
    _insert_denial(migrated_conn, assertion_id=82011, valid_from=None)
    with patch("cortex_store.terminal_facts.TERMINAL_FACTS_CAP", 4):
        block, _ = resolve_terminal_facts(migrated_conn, _HUB)
    assert block is not None
    ids = [fact.assertion_id for fact in block.facts]
    assert ids[:3] == [82000, 82001, 82002]
    assert ids[3:] == [82010]
