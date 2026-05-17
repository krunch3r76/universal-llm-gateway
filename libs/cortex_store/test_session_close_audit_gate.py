"""v1.3.1 Path 3 session_close audit gate (advisory only)."""
from __future__ import annotations
import sqlite3
from cortex_store.session_close_validation import _audit_normalization_refusals_for_session
def test_audit_gate_returns_list_never_raises() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE assertions (id INTEGER, entity_id TEXT, raw_predicate_form TEXT, normalization_decision TEXT, candidate_set_fingerprint TEXT, evidence TEXT, superseded_by INTEGER);")
    # no rows -> []
    out = _audit_normalization_refusals_for_session(conn, "web-2026-05-17-0000")
    assert isinstance(out, list)
    # bad session still []
    out2 = _audit_normalization_refusals_for_session(conn, "")
    assert out2 == []
