"""Entity-lifecycle vocabulary grammar and structural anti-pattern detectors.

Compiles checkable rules from ``agent_skill:entity-lifecycle-discipline``
(§Vocabulary — step vs phase; §Anti-patterns — todos are leaves) into
WARNING-tier graph-only audit gates. Two kinds:

  * ``entity_vocabulary_grammar`` — G1/G2/G3 sub-patterns
  * ``entity_structural_antipattern`` — A2 sub-pattern (task→project child_of)

Wave-3 mechanical bin: ``todo:entity-lifecycle-structural-validators``.
"""

from __future__ import annotations

import json
from typing import Any

from ...db import query
from ._shared import _finding


def detect_entity_vocabulary_grammar(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Flag G1 step entities, G2 plan_phase→task child_of, G3 todo→todo child_of."""
    findings: list[dict[str, Any]] = []

    g1_clauses = ["(id LIKE 'step:%' OR type = 'step')"]
    g1_params: list[Any] = []
    if subject:
        g1_clauses.append("id = ?")
        g1_params.append(subject)
    g1_sql = f"SELECT id, type FROM entities WHERE {' AND '.join(g1_clauses)}"
    for row in query(conn, g1_sql, tuple(g1_params)):
        findings.append(
            _finding(
                "entity_vocabulary_grammar",
                row["id"],
                f"G1: entity {row['id']} uses non-existent 'step' type — "
                "steps are inline body items on a todo, not entities",
            )
        )

    edge_subject_clause = ""
    edge_params: list[Any] = []
    if subject:
        edge_subject_clause = " AND (from_entity = ? OR to_entity = ?)"
        edge_params.extend([subject, subject])

    g2_sql = (
        "SELECT from_entity, to_entity FROM relationships "
        "WHERE active=1 AND type='child_of' "
        "AND from_entity LIKE 'plan_phase:%' AND to_entity LIKE 'task:%'"
        f"{edge_subject_clause}"
    )
    for row in query(conn, g2_sql, tuple(edge_params)):
        frm, to = row["from_entity"], row["to_entity"]
        findings.append(
            _finding(
                "entity_vocabulary_grammar",
                frm,
                f"G2: plan_phase {frm} child_of task {to} — "
                "phases belong to plans; rehome under a plan: root",
            )
        )

    g3_sql = (
        "SELECT from_entity, to_entity FROM relationships "
        "WHERE active=1 AND type='child_of' "
        "AND from_entity LIKE 'todo:%' AND to_entity LIKE 'todo:%'"
        f"{edge_subject_clause}"
    )
    for row in query(conn, g3_sql, tuple(edge_params)):
        frm, to = row["from_entity"], row["to_entity"]
        findings.append(
            _finding(
                "entity_vocabulary_grammar",
                frm,
                f"G3: todo {frm} child_of todo {to} — todos are leaves; "
                "regroup under a task: root (child_of a task, or related_to for grouping)",
            )
        )

    return findings


def _parse_attributes(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        if isinstance(raw, str):
            parsed = json.loads(raw) if raw else {}
        else:
            parsed = raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def detect_entity_structural_antipattern(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Flag A2 task→project child_of edges unless portfolio_child_of=true on task."""
    clauses = [
        "active=1",
        "type='child_of'",
        "from_entity LIKE 'task:%'",
        "to_entity LIKE 'project:%'",
    ]
    params: list[Any] = []
    if subject:
        clauses.append("(from_entity = ? OR to_entity = ?)")
        params.extend([subject, subject])

    sql = (
        "SELECT from_entity, to_entity FROM relationships "
        f"WHERE {' AND '.join(clauses)}"
    )
    rows = query(conn, sql, tuple(params))

    findings: list[dict[str, Any]] = []
    for row in rows:
        task_id = row["from_entity"]
        project_id = row["to_entity"]
        attr_rows = query(
            conn,
            "SELECT attributes FROM entities WHERE id = ?",
            (task_id,),
        )
        attrs = _parse_attributes(attr_rows[0]["attributes"]) if attr_rows else {}
        if attrs.get("portfolio_child_of") is True:
            continue
        findings.append(
            _finding(
                "entity_structural_antipattern",
                task_id,
                f"A2: task {task_id} child_of project {project_id} — "
                "use related_to unless an intentional portfolio hierarchy "
                f"(then set attributes.portfolio_child_of=true on {task_id})",
            )
        )
    return findings


__all__ = [
    "detect_entity_structural_antipattern",
    "detect_entity_vocabulary_grammar",
]
