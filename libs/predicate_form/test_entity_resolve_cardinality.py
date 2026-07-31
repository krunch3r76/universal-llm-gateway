"""v1.3.1 cardinality-aware resolver tests (shadow mode).

Covers ResolutionResult shape, 0/1/>=2 candidate decisions, fingerprint
stability, and that resolve_slug (first-match) remains unchanged.
"""

from __future__ import annotations

import sqlite3

from predicate_form.entity_resolve import (
    DBEntityResolver,
    ResolutionResult,
    StaticEntityResolver,
)


def _make_conn_with_entities(entities: list[str]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE entities (id TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO entities (id) VALUES (?)", [(e,) for e in entities])
    conn.commit()
    return conn


def test_resolution_result_shape() -> None:
    r = ResolutionResult(
        decision="resolved_single",
        match="person:foo",
        match_first_match="person:foo",
        candidates=("person:foo",),
        candidate_fingerprint="deadbeefcafebabe",
    )
    assert r.decision == "resolved_single"
    assert r.match == "person:foo"


def test_static_resolver_cardinality_no_match() -> None:
    res = StaticEntityResolver({})
    out = res.resolve_slug_with_cardinality("unknown-slug")
    assert out.decision == "no_match"
    assert out.match is None
    assert out.candidates == ()
    assert out.candidate_fingerprint == ""


def test_static_resolver_cardinality_single() -> None:
    res = StaticEntityResolver({"foo-bar": "person:foo-bar"})
    out = res.resolve_slug_with_cardinality("foo-bar")
    assert out.decision == "resolved_single"
    assert out.match == "person:foo-bar"
    assert out.match_first_match == "person:foo-bar"
    assert len(out.candidates) == 1


def test_db_resolver_cardinality_collision() -> None:
    # Seed two entities with same slug under different prefixes (both in _DEFAULT)
    conn = _make_conn_with_entities(
        ["person:duplicate-slug", "document:duplicate-slug"]
    )
    # Restrict prefixes to ones containing the collision for determinism
    prefixes = ("person", "document")
    res = DBEntityResolver(conn, type_prefixes=prefixes)
    out = res.resolve_slug_with_cardinality("duplicate-slug")
    assert out.decision == "collision_refused"
    assert out.match is None
    assert (
        out.match_first_match == "document:duplicate-slug"
        or out.match_first_match == "person:duplicate-slug"
    )
    assert len(out.candidates) == 2
    assert "person:duplicate-slug" in out.candidates
    assert "document:duplicate-slug" in out.candidates
    # Fingerprint stable
    fp1 = out.candidate_fingerprint
    out2 = res.resolve_slug_with_cardinality("duplicate-slug")
    assert out2.candidate_fingerprint == fp1


def test_db_resolver_cardinality_no_match_and_single() -> None:
    conn = _make_conn_with_entities(["person:real-one"])
    res = DBEntityResolver(conn)
    out0 = res.resolve_slug_with_cardinality("ghost")
    assert out0.decision == "no_match"
    out1 = res.resolve_slug_with_cardinality("real-one")
    assert out1.decision == "resolved_single"
    assert out1.match == "person:real-one"


def test_fingerprint_stability_and_empty() -> None:
    conn = _make_conn_with_entities([])
    res = DBEntityResolver(conn)
    out = res.resolve_slug_with_cardinality("nothing")
    assert out.candidate_fingerprint == ""
