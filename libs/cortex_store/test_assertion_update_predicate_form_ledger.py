"""v1.3.1 ledger population on assertion_update path (predicate_form writeback).

Regression test for the bug where _update.py dropped the four normalization-decision
ledger columns (raw_predicate_form, normalization_decision, candidate_set_fingerprint,
normalizer_version) even though it called _normalize_predicate_form_for_write and had
normalize_result in hand. Mirrors the extraction in _create.py:184-187.
"""

from __future__ import annotations

import sqlite3

import pytest

from predicate_form import NORMALIZER_VERSION
from cortex_store.routes.assertions import _update_assertion_impl

_ENTITY = "person:camelia-mahmoudi"


def _seed_entities(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
        (_ENTITY, "person", "camelia-mahmoudi"),
    )
    conn.commit()


@pytest.fixture()
def conn(migrated_conn: sqlite3.Connection) -> sqlite3.Connection:
    _seed_entities(migrated_conn)
    return migrated_conn


def _patch_cortex_conn(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    """Patch the cortex_conn used inside the update route module."""
    monkeypatch.setattr(
        "cortex_store.routes.assertions._update.cortex_conn", lambda: conn
    )


def test_assertion_update_populates_all_four_ledger_columns(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH with explicit predicate_form must write the 4 ledger columns computed by normalize.

    Uses head-schema DB via migrated_conn; calls the real _update_assertion_impl
    (which exercises update_assertion + normalize + SET builder).
    """
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form) "
        "VALUES (?, ?, ?, ?)",
        (
            _ENTITY,
            "Phase D claim for ledger update test.",
            "confirmed",
            None,
        ),
    )
    aid = conn.execute("SELECT id FROM assertions").fetchone()["id"]
    conn.commit()

    _patch_cortex_conn(monkeypatch, conn)

    legacy = "role(camelia_mahmoudi, filer, 24PR197054)"
    result = _update_assertion_impl(aid, {"predicate_form": legacy})

    # The response item has the canonical
    assert (
        result["predicate_form"] == "role(person:camelia-mahmoudi, filer, 24pr197054)"
    )

    # Now verify ledger columns were written by the UPDATE path
    row = conn.execute(
        "SELECT raw_predicate_form, normalization_decision, candidate_set_fingerprint, normalizer_version "
        "FROM assertions WHERE id = ?",
        (aid,),
    ).fetchone()

    assert row["raw_predicate_form"] == legacy
    assert row["normalization_decision"] in ("resolved_single", "no_match")
    assert row[
        "candidate_set_fingerprint"
    ]  # non-empty for this case (has eligible arg)
    assert row["normalizer_version"] == NORMALIZER_VERSION

    # Also ensure the read model path works (via full select in update)
    assert (
        "raw_predicate_form" in result
    )  # flattened in _update_assertion_impl? wait, no — item has it
    # The returned result from impl is the item_dump + optional envelope; item has the ledger via AssertionItem
    assert result.get("raw_predicate_form") == legacy


def test_assertion_update_without_predicate_form_leaves_ledger_untouched(
    conn: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-pf update must not touch (or nullify) ledger columns."""
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "raw_predicate_form, normalization_decision, candidate_set_fingerprint, normalizer_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _ENTITY,
            "claim",
            "confirmed",
            "role(person:camelia-mahmoudi, filer, 24pr197054)",
            "role(camelia_mahmoudi, filer, 24PR197054)",
            "resolved_single",
            "fingerprint123",
            "v1.3.1",
        ),
    )
    aid = conn.execute("SELECT id FROM assertions").fetchone()["id"]
    conn.commit()

    _patch_cortex_conn(monkeypatch, conn)

    # Update something else — ledger must survive unchanged
    result = _update_assertion_impl(aid, {"review_status": "staged"})

    row = conn.execute(
        "SELECT raw_predicate_form, normalization_decision FROM assertions WHERE id = ?",
        (aid,),
    ).fetchone()
    assert row["raw_predicate_form"] == "role(camelia_mahmoudi, filer, 24PR197054)"
    assert row["normalization_decision"] == "resolved_single"
    assert result.get("review_status") == "staged"
