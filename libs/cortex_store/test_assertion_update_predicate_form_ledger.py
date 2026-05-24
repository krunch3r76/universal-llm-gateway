"""v1.3.1 ledger population on assertion_update path (predicate_form writeback).

Regression test for the bug where _update.py dropped the four normalization-decision
ledger columns (raw_predicate_form, normalization_decision, candidate_set_fingerprint,
normalizer_version) even though it called _normalize_predicate_form_for_write and had
normalize_result in hand. Mirrors the extraction in _create.py:184-187.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from cortex_store.routes.assertions import _update_assertion_impl


def _load_migration():
    path = Path(__file__).parent / "migrations" / "039_normalization_decision_ledger.py"
    spec = importlib.util.spec_from_file_location("mig039", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _patch_cortex_conn(
    monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection
) -> None:
    """Patch the cortex_conn used inside the update route module."""
    monkeypatch.setattr(
        "cortex_store.routes.assertions._update.cortex_conn", lambda: conn
    )


def _seed_entities(conn: sqlite3.Connection, ids: list[str]) -> None:
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, type TEXT);"
    )
    conn.executemany(
        "INSERT OR IGNORE INTO entities (id, type) VALUES (?, ?)",
        [(i, i.split(":")[0]) for i in ids],
    )
    conn.commit()


def test_assertion_update_populates_all_four_ledger_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PATCH with explicit predicate_form must write the 4 ledger columns computed by normalize.

    Uses in-memory DB + migration 039 to ensure columns exist; calls the real
    _update_assertion_impl (which exercises update_assertion + normalize + SET builder).
    """
    mig = _load_migration()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Base table (pre-039 shape) + entities; migration will add the ledger cols
    conn.executescript(
        """
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            confidence TEXT NOT NULL,
            predicate_form TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        );
        """
    )
    _seed_entities(conn, ["person:camelia-mahmoudi"])
    mig.migrate(conn)

    # Insert a starter assertion (no ledger yet — pre-fix rows stay NULL, but we update it)
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form) "
        "VALUES (?, ?, ?, ?)",
        (
            "person:camelia-mahmoudi",
            "Phase D claim for ledger update test.",
            "confirmed",
            None,
        ),
    )
    aid = conn.execute("SELECT id FROM assertions").fetchone()["id"]

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
    assert row["normalizer_version"] == "v1.3.1"

    # Also ensure the read model path works (via full select in update)
    assert (
        "raw_predicate_form" in result
    )  # flattened in _update_assertion_impl? wait, no — item has it
    # The returned result from impl is the item_dump + optional envelope; item has the ledger via AssertionItem
    assert result.get("raw_predicate_form") == legacy


def test_assertion_update_without_predicate_form_leaves_ledger_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-pf update must not touch (or nullify) ledger columns."""
    mig = _load_migration()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            confidence TEXT NOT NULL,
            predicate_form TEXT,
            raw_predicate_form TEXT,
            normalization_decision TEXT,
            candidate_set_fingerprint TEXT,
            normalizer_version TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT
        );
        """
    )
    _seed_entities(conn, ["person:camelia-mahmoudi"])
    mig.migrate(conn)  # idempotent

    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        "raw_predicate_form, normalization_decision, candidate_set_fingerprint, normalizer_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "person:camelia-mahmoudi",
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
