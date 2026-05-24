"""v1.3.1 ledger write-path tests (via normalize + shape)."""

from __future__ import annotations

import sqlite3

from predicate_form import normalize_predicate_domain
from predicate_form.entity_resolve import DBEntityResolver


def _seed_entities(conn: sqlite3.Connection, ids: list[str]) -> None:
    conn.executescript("CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY);")
    conn.executemany(
        "INSERT OR IGNORE INTO entities (id) VALUES (?)", [(i,) for i in ids]
    )
    conn.commit()


def test_normalize_returns_ledger_fields_on_single() -> None:
    conn = sqlite3.connect(":memory:")
    _seed_entities(conn, ["person:camelia-mahmoudi"])
    res = normalize_predicate_domain(
        "person:test",
        "status(camelia_mahmoudi, ready)",
        "claim",
        resolver=DBEntityResolver(conn),
    )
    assert "raw_predicate_form" in res
    assert res["normalization_decision"] in ("resolved_single", "no_match")
    assert "candidate_set_fingerprint" in res
    assert res["normalizer_version"] == "v1.3.1"


def test_normalize_ledger_no_eligible_args() -> None:
    conn = sqlite3.connect(":memory:")
    res = normalize_predicate_domain(
        "person:x", "has_attribute(123, 456)", "c", resolver=DBEntityResolver(conn)
    )
    assert res["normalization_decision"] == "resolved_single"
    assert res["candidate_set_fingerprint"] == ""
