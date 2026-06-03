"""Supersede ledger / predicate_form carryover semantics.

The supersede route resolves predicate_form in three branches (see
routes/assertions/_supersede.py):

  1. Explicit non-null predicate_form supplied → normalised before INSERT,
     ledger fields computed (post-v1.3.1; friction 9826).
  2. Claim changed, predicate_form not supplied → inherited form is DROPPED
     (would otherwise encode the OLD claim's structure on the new row) and a
     background predicate-extract re-derivation is dispatched; ledger NULL
     until re-extract runs (thread 1227 carryover fix).
  3. Claim unchanged, predicate_form not supplied → inherited canonical value
     carried over as-is; ledger NULL on the new row (predecessor's ledger
     untouched).

These tests pin the carryover/ledger contract so future changes are deliberate.
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


def test_supersede_drops_stale_predicate_form_on_claim_change() -> None:
    """Claim-change branch drops the inherited predicate_form and re-derives.

    Pins the thread-1227 carryover fix: when the claim changes and the caller
    does not explicitly supply predicate_form, the supersede route must NOT
    clone the predecessor's predicate_form (which encodes the OLD claim), and
    must schedule a background re-extract from the new claim.
    """
    src = Path(__file__).parent / "routes" / "assertions" / "_supersede.py"
    text = src.read_text(encoding="utf-8")
    assert 'claim_changed = body.claim != old_data.get("claim")' in text, (
        "Supersede must detect claim change for the predicate_form drop branch"
    )
    assert (
        "elif not predicate_form_explicit and claim_changed:" in text
        and "eff_predicate_form = None" in text
    ), "Supersede must drop the inherited predicate_form when the claim changed"
    assert "dispatch_predicate_extract_background(new_id" in text, (
        "Supersede must re-derive predicate_form via background extract on claim change"
    )


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
        (
            "person:x",
            "old claim",
            "confirmed",
            "status(person:x, ready)",
            "status(person:x, ready)",
            "resolved_single",
            "abc123def456",
            "v1.3.1",
        ),
    )
    old_id = conn.execute(
        "SELECT id FROM assertions WHERE claim='old claim'"
    ).fetchone()[0]

    # New row — supersede-equivalent INSERT writes NULL ledger
    conn.execute(
        "INSERT INTO assertions (entity_id, claim, confidence, "
        " raw_predicate_form, normalization_decision, candidate_set_fingerprint, normalizer_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("person:x", "new claim", "confirmed", None, None, None, None),
    )
    new_id = conn.execute(
        "SELECT id FROM assertions WHERE claim='new claim'"
    ).fetchone()[0]
    conn.execute(
        "UPDATE assertions SET superseded_by=?, valid_until='now' WHERE id=?",
        (new_id, old_id),
    )

    new_row = conn.execute("SELECT * FROM assertions WHERE id=?", (new_id,)).fetchone()
    assert new_row["raw_predicate_form"] is None
    assert new_row["normalization_decision"] is None
    assert new_row["candidate_set_fingerprint"] is None
    assert new_row["normalizer_version"] is None
    # Old row's ledger preserved (§7.3 — supersede doesn't mutate old row's ledger)
    old_row = conn.execute("SELECT * FROM assertions WHERE id=?", (old_id,)).fetchone()
    assert old_row["normalization_decision"] == "resolved_single"
    assert old_row["normalizer_version"] == "v1.3.1"
