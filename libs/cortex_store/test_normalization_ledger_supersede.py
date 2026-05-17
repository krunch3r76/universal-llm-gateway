"""v1.3.1 supersede ledger semantics — documents scope decision.

The v1.3.1 supersede route does NOT take predicate_form in its request body
and therefore does not invoke _normalize_predicate_form_for_write. Per the
work-order, ledger fields on superseded rows are NULL — this is consistent
with "pre-ledger rows stay NULL" and the shadow-mode invariant. A future
release that extends SupersedeRequest with predicate_form (or moves
predicate_form normalization to a sync step independent of the request body)
would change this; for v1.3.1 the contract is NULL ledger on supersession.

This test pins the contract so future changes are deliberate.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path


def _load_migration() -> object:
    path = Path(__file__).parent / "migrations" / "039_normalization_decision_ledger.py"
    spec = importlib.util.spec_from_file_location("mig039", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_supersede_writes_null_ledger_fields_documented_scope() -> None:
    """Supersede route INSERT explicitly writes None for all 4 ledger columns.

    Verifies the literal NULLs in libs/cortex_store/routes/assertions/_supersede.py
    INSERT statement match the documented scope decision. If this test fails because
    the INSERT now passes computed values, the supersede surface has been extended
    (likely in a release after v1.3.1) and this test should be replaced with the
    fresh-compute assertions per work-order §5.
    """
    src = Path(__file__).parent / "routes" / "assertions" / "_supersede.py"
    text = src.read_text(encoding="utf-8")
    # The INSERT must list the 4 ledger columns AND must pass them as None literals.
    assert "raw_predicate_form, normalization_decision, candidate_set_fingerprint, normalizer_version" in text, \
        "Supersede INSERT must list the 4 ledger columns"
    # Count how many `None,` followed by another None,None,None appear right before the closing paren —
    # we just check the textual block exists; tighter pinning would be too brittle.
    assert "None,\n                    None,\n                    None,\n                    None," in text, \
        "Supersede INSERT must pass None for the 4 ledger columns in v1.3.1"


def test_supersede_path_round_trip_leaves_ledger_null() -> None:
    """Integration-shaped: a row created with non-NULL ledger, when superseded,
    produces a new row whose ledger fields read back as NULL.

    Uses raw SQL to emulate the INSERT shape used by both routes without
    requiring the full FastAPI stack. The migration runs first to add the
    columns; then we INSERT one row with values and a second row with NULLs,
    UPDATE the first's superseded_by to point at the second, and read back.
    """
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
            superseded_by INTEGER,
            valid_until TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    mig.migrate(conn)

    # Old row — created via POST-equivalent INSERT with non-NULL ledger
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, predicate_form, "
        " raw_predicate_form, normalization_decision, candidate_set_fingerprint, normalizer_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("person:x", "old claim", "confirmed", "status(person:x, ready)",
         "status(person:x, ready)", "resolved_single", "abc123def456", "v1.3.1"),
    )
    old_id = conn.execute("SELECT id FROM assertions WHERE claim='old claim'").fetchone()[0]

    # New row — supersede-equivalent INSERT writes NULL ledger
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, "
        " raw_predicate_form, normalization_decision, candidate_set_fingerprint, normalizer_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("person:x", "new claim", "confirmed", None, None, None, None),
    )
    new_id = conn.execute("SELECT id FROM assertions WHERE claim='new claim'").fetchone()[0]
    conn.execute("UPDATE assertions SET superseded_by=?, valid_until='now' WHERE id=?", (new_id, old_id))

    new_row = conn.execute("SELECT * FROM assertions WHERE id=?", (new_id,)).fetchone()
    assert new_row["raw_predicate_form"] is None
    assert new_row["normalization_decision"] is None
    assert new_row["candidate_set_fingerprint"] is None
    assert new_row["normalizer_version"] is None
    # Old row's ledger preserved (§7.3 — supersede doesn't mutate old row's ledger)
    old_row = conn.execute("SELECT * FROM assertions WHERE id=?", (old_id,)).fetchone()
    assert old_row["normalization_decision"] == "resolved_single"
    assert old_row["normalizer_version"] == "v1.3.1"
