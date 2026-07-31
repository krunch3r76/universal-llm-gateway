"""Unit tests for the todo_implementation_seed_incomplete audit gate.

Verifies registry-driven seed checks (source_uri / required_skills / context edge)
and suppression conditions (deferred, done, backlog=true, seed_contract_ack).
Context edges are defined as active relationships whose target is NOT an
agent_skill entity — skill-only edges are insufficient for the seed contract.

Grounded in: decision:todo-creation-rich-seed-contract (thread 1144);
tasks/specs/implement-input-schema.md §3.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from cortex_store.dispatch_ops._detectors.todo import (
    detect_todo_implementation_seed_incomplete,
)

_KIND = "todo_implementation_seed_incomplete"

_TODO_SCHEMA = {
    "required_keys": "[]",
    "optional_keys": json.dumps(
        ["files_expected", "acceptance_criteria", "required_skills", "multi_phase_arc"]
    ),
    "enum_constraints": json.dumps({"multi_phase_arc": [True, False]}),
    "notes": "seed contract test fixture",
}


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(
        """
        CREATE TABLE type_attribute_schemas (
            entity_type TEXT PRIMARY KEY,
            required_keys TEXT NOT NULL,
            optional_keys TEXT NOT NULL,
            enum_constraints TEXT NOT NULL,
            notes TEXT
        );
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT,
            source_uri TEXT,
            workflow_state TEXT,
            attributes TEXT
        );
        CREATE TABLE relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            from_entity TEXT NOT NULL,
            to_entity TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );
        """
    )
    c.execute(
        "INSERT INTO type_attribute_schemas "
        "(entity_type, required_keys, optional_keys, enum_constraints, notes) "
        "VALUES ('todo', ?, ?, ?, ?)",
        (
            _TODO_SCHEMA["required_keys"],
            _TODO_SCHEMA["optional_keys"],
            _TODO_SCHEMA["enum_constraints"],
            _TODO_SCHEMA["notes"],
        ),
    )
    return c


def _add_todo(
    conn,
    todo_id: str,
    *,
    workflow_state: str = "open",
    source_uri: str | None = None,
    required_skills: list | None = None,
    extra_attrs: dict | None = None,
) -> None:
    attrs: dict = {}
    if required_skills is not None:
        attrs["required_skills"] = required_skills
    if extra_attrs:
        attrs.update(extra_attrs)
    conn.execute(
        "INSERT INTO entities (id, type, name, source_uri, workflow_state, attributes) "
        "VALUES (?, 'todo', ?, ?, ?, ?)",
        (
            todo_id,
            todo_id,
            source_uri,
            workflow_state,
            json.dumps(attrs) if attrs else None,
        ),
    )


def _add_rel(
    conn, from_id: str, to_id: str, rel_type: str = "references", active: int = 1
) -> None:
    conn.execute(
        "INSERT INTO relationships (type, from_entity, to_entity, active) VALUES (?, ?, ?, ?)",
        (rel_type, from_id, to_id, active),
    )


# --- fully seeded (no finding) -------------------------------------------


def test_fully_seeded_todo_no_finding(conn: sqlite3.Connection) -> None:
    """A todo with source_uri + required_skills + a context edge is clean."""
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "todo:t1", "decision:some-decision")
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_in_progress_fully_seeded_no_finding(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        workflow_state="in_progress",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "todo:t1", "service:some-service", rel_type="relates_to")
    assert detect_todo_implementation_seed_incomplete(conn) == []


# --- suppressed states (no finding) --------------------------------------


def test_done_todo_not_flagged(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", workflow_state="done")
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_deferred_todo_not_flagged(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", workflow_state="deferred")
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_cancelled_todo_not_flagged(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", workflow_state="cancelled")
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_blocked_todo_not_flagged(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", workflow_state="blocked")
    assert detect_todo_implementation_seed_incomplete(conn) == []


# --- attribute-level suppression (no finding) ----------------------------


def test_backlog_true_suppresses(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", extra_attrs={"backlog": True})
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_seed_contract_ack_suppresses(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        extra_attrs={"seed_contract_ack": "known backlog — no spec needed yet"},
    )
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_recon_pending_density_suppresses(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        extra_attrs={"density_triage": "recon_pending"},
    )
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_seed_contract_ack_empty_string_suppresses(conn: sqlite3.Connection) -> None:
    """Any non-None value for seed_contract_ack is an escape hatch."""
    _add_todo(conn, "todo:t1", extra_attrs={"seed_contract_ack": ""})
    assert detect_todo_implementation_seed_incomplete(conn) == []


# --- individual gaps (one finding each) ----------------------------------


def test_missing_source_uri_one_finding(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", required_skills=["service-lifecycle"])
    _add_rel(conn, "todo:t1", "decision:d1")
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == _KIND
    assert f["subject"] == "todo:t1"
    assert "source_uri" in f["detail"]
    assert "required_skills" not in f["detail"].split("source_uri")[0]


def test_empty_source_uri_flagged(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", source_uri="   ", required_skills=["service-lifecycle"])
    _add_rel(conn, "todo:t1", "decision:d1")
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    assert "source_uri" in findings[0]["detail"]


def test_missing_required_skills_one_finding(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1", source_uri="tasks/specs/t1.md")
    _add_rel(conn, "todo:t1", "decision:d1")
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    assert "required_skills" in findings[0]["detail"]


def test_empty_required_skills_list_flagged(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=[],
    )
    _add_rel(conn, "todo:t1", "decision:d1")
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    assert "required_skills" in findings[0]["detail"]


def test_missing_context_edge_one_finding(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    # No relationships at all.
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    assert "context edge" in findings[0]["detail"]


# --- skill-only edges do NOT satisfy context-edge requirement ------------


def test_skill_edge_only_still_flags_context_edge(conn: sqlite3.Connection) -> None:
    """An agent_skill requires edge proves skill hygiene but not substrate context."""
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "todo:t1", "agent_skill:service-lifecycle", rel_type="requires")
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    assert "context edge" in findings[0]["detail"]


def test_skill_edge_plus_context_edge_no_finding(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "todo:t1", "agent_skill:service-lifecycle", rel_type="requires")
    _add_rel(conn, "todo:t1", "decision:d1", rel_type="references")
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_inactive_context_edge_does_not_satisfy(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "todo:t1", "decision:d1", active=0)
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    assert "context edge" in findings[0]["detail"]


# --- all three gaps (one finding with all listed) ------------------------


def test_all_three_gaps_one_finding(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1")  # no source_uri, no required_skills, no edges
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    detail = findings[0]["detail"]
    assert "source_uri" in detail
    assert "required_skills" in detail
    assert "context edge" in detail


# --- context edge relationship types ------------------------------------


def test_relates_to_service_satisfies_context_edge(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "todo:t1", "service:my-service", rel_type="relates_to")
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_evidence_uris_thread_satisfies_context_edge(conn: sqlite3.Connection) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "todo:t1", "agent-bus:1142", rel_type="evidence_uris")
    assert detect_todo_implementation_seed_incomplete(conn) == []


# --- canonicalized symmetric edges (todo as target) ---------------------


def test_canonicalized_related_to_todo_as_target_satisfies(
    conn: sqlite3.Connection,
) -> None:
    """related_to is lexicographically canonicalized: decision:/service: sort
    before todo:, so the todo lands as *target*. The edge must still count."""
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    # Canonicalized: decision: < todo:, so todo is the to_entity.
    _add_rel(conn, "decision:d1", "todo:t1", rel_type="related_to")
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_canonicalized_service_edge_todo_as_target_satisfies(
    conn: sqlite3.Connection,
) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "service:my-service", "todo:t1", rel_type="related_to")
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_skill_edge_with_todo_as_target_still_flags(
    conn: sqlite3.Connection,
) -> None:
    """A skill edge incident to the todo (todo as either endpoint) does not
    count — the other endpoint is an agent_skill."""
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    # agent_skill: < todo: so canonicalization would put skill as source.
    _add_rel(conn, "agent_skill:service-lifecycle", "todo:t1", rel_type="requires")
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    assert "context edge" in findings[0]["detail"]


def test_inactive_canonicalized_edge_does_not_satisfy(
    conn: sqlite3.Connection,
) -> None:
    _add_todo(
        conn,
        "todo:t1",
        source_uri="tasks/specs/t1.md",
        required_skills=["service-lifecycle"],
    )
    _add_rel(conn, "decision:d1", "todo:t1", rel_type="related_to", active=0)
    findings = detect_todo_implementation_seed_incomplete(conn)
    assert len(findings) == 1
    assert "context edge" in findings[0]["detail"]


# --- subject filter ------------------------------------------------------


def test_subject_filter_scopes_to_one_entity(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1")
    _add_todo(conn, "todo:t2")
    findings = detect_todo_implementation_seed_incomplete(conn, subject="todo:t1")
    assert len(findings) == 1
    assert findings[0]["subject"] == "todo:t1"


def test_subject_filter_no_match(conn: sqlite3.Connection) -> None:
    _add_todo(conn, "todo:t1")
    findings = detect_todo_implementation_seed_incomplete(conn, subject="todo:other")
    assert findings == []


# --- non-todo entities not affected -------------------------------------


def test_non_todo_entity_not_flagged(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO entities (id, type, name, workflow_state) "
        "VALUES ('project:p1', 'project', 'P1', 'open')",
    )
    assert detect_todo_implementation_seed_incomplete(conn) == []


def test_no_registry_row_skips_detector(conn: sqlite3.Connection) -> None:
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    bare.executescript(
        """
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT,
            source_uri TEXT,
            workflow_state TEXT,
            attributes TEXT
        );
        """
    )
    _add_todo(bare, "todo:t1")
    assert detect_todo_implementation_seed_incomplete(bare) == []
