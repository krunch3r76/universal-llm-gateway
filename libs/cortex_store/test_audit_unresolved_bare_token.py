"""v1.3.1 Path 2 detector test."""
from __future__ import annotations
import sqlite3
from cortex_store.dispatch_ops._detectors.predicate_form import detect_unresolved_bare_token_in_predicate_form
def test_detector_skips_null_ledger_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE assertions (id INTEGER, entity_id TEXT, raw_predicate_form TEXT, normalization_decision TEXT, candidate_set_fingerprint TEXT, created_at TEXT, superseded_by INTEGER); INSERT INTO assertions (id,entity_id,normalization_decision,superseded_by) VALUES (1,'p:x',NULL,NULL);")
    findings = detect_unresolved_bare_token_in_predicate_form(conn)
    assert findings == []  # NULL rows skipped
