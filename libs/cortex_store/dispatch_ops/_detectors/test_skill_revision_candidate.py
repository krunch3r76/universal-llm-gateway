"""Unit tests for skill-revision-candidate audit detector."""

from __future__ import annotations

import sqlite3

import pytest

from cortex_store.dispatch_ops._detectors.skill_revision_candidate import (
    detect_agent_skill_revision_candidate_unadjudicated,
)
from cortex_store.dispatch_ops.ops_audit_detectors import GRAPH_ONLY_KINDS

_KIND = "agent_skill_revision_candidate_unadjudicated"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT
        );
        CREATE TABLE assertions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            claim TEXT NOT NULL,
            superseded_by INTEGER,
            review_status TEXT
        );
        """
    )
    return c


def _add_skill(conn: sqlite3.Connection, slug: str) -> str:
    eid = f"agent_skill:{slug}"
    conn.execute(
        "INSERT INTO entities (id, type, name) VALUES (?, 'agent_skill', ?)",
        (eid, slug),
    )
    return eid


def _add_assertion(
    conn: sqlite3.Connection,
    entity_id: str,
    claim: str,
    *,
    superseded_by: int | None = None,
    review_status: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO assertions (entity_id, claim, superseded_by, review_status) "
        "VALUES (?, ?, ?, ?)",
        (entity_id, claim, superseded_by, review_status),
    )
    return int(cur.lastrowid)


def test_kind_in_graph_only() -> None:
    assert _KIND in GRAPH_ONLY_KINDS


def test_match_on_prefix(conn: sqlite3.Connection) -> None:
    eid = _add_skill(conn, "cheap-recon")
    aid = _add_assertion(conn, eid, "CANDIDATE SKILL REVISION: tighten axis-2")
    findings = detect_agent_skill_revision_candidate_unadjudicated(conn)
    assert len(findings) == 1
    assert findings[0]["kind"] == _KIND
    assert findings[0]["subject"] == eid
    assert str(aid) in findings[0]["detail"]


def test_no_match_non_prefix(conn: sqlite3.Connection) -> None:
    eid = _add_skill(conn, "foo")
    _add_assertion(conn, eid, "CANDIDATE panel reviewer for thread 99")
    assert detect_agent_skill_revision_candidate_unadjudicated(conn) == []


def test_no_match_when_superseded(conn: sqlite3.Connection) -> None:
    eid = _add_skill(conn, "bar")
    _add_assertion(
        conn,
        eid,
        "CANDIDATE SKILL REVISION old",
        superseded_by=99,
    )
    assert detect_agent_skill_revision_candidate_unadjudicated(conn) == []


def test_no_match_terminal_review_status(conn: sqlite3.Connection) -> None:
    eid = _add_skill(conn, "baz")
    _add_assertion(
        conn,
        eid,
        "CANDIDATE SKILL REVISION done",
        review_status="committed",
    )
    _add_assertion(
        conn,
        eid,
        "CANDIDATE SKILL REVISION rejected",
        review_status="rejected",
    )
    assert detect_agent_skill_revision_candidate_unadjudicated(conn) == []


def test_match_null_flagged_staged_review_status(conn: sqlite3.Connection) -> None:
    eid = _add_skill(conn, "qux")
    _add_assertion(conn, eid, "CANDIDATE SKILL REVISION open", review_status=None)
    _add_assertion(conn, eid, "CANDIDATE SKILL REVISION flagged", review_status="flagged")
    _add_assertion(conn, eid, "CANDIDATE SKILL REVISION staged", review_status="staged")
    findings = detect_agent_skill_revision_candidate_unadjudicated(conn)
    assert len(findings) == 1
    assert "3 unadjudicated" in findings[0]["detail"]


def test_subject_filter(conn: sqlite3.Connection) -> None:
    e1 = _add_skill(conn, "one")
    e2 = _add_skill(conn, "two")
    _add_assertion(conn, e1, "CANDIDATE SKILL REVISION a")
    _add_assertion(conn, e2, "CANDIDATE SKILL REVISION b")
    findings = detect_agent_skill_revision_candidate_unadjudicated(conn, subject=e1)
    assert len(findings) == 1
    assert findings[0]["subject"] == e1
