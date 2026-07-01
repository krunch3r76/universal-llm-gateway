#!/usr/bin/env python3
"""One-shot remediation for entity-grammar G3/A2 structural debt.

Remediates todo→todo child_of (G3) and task→project child_of (A2) findings
surfaced by entity_grammar.py detectors. Idempotent on re-run (skips existing
target edges; ignores already-deleted G3 rows).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any

sys.path.insert(0, "libs")

from cortex_store.dispatch_ops import execute_op
from cortex_store.schema_snapshot import open_live_db_readonly
from cortex_store.dispatch_ops._detectors.entity_grammar import (
    detect_entity_structural_antipattern,
    detect_entity_vocabulary_grammar,
)


def _ro_conn() -> sqlite3.Connection:
    conn = open_live_db_readonly()
    conn.row_factory = sqlite3.Row
    return conn

AGENT = "cursor"
SESSION = "cursor-remediate-entity-lifecycle-g3-a2"

_IMPLEMENT_ATTRS = frozenset(
    {
        "files_expected",
        "acceptance_criteria",
        "required_skills",
        "implement_ready_assertion_id",
        "density_triage",
        "recon_status",
    }
)

# A2: task→project child_of → related_to (tracks under umbrella project)
A2_RELATED_TO = [
    ("task:rule-manifest-track-b", "project:agent-workflow-parity", "track"),
    ("task:skill-server-mcp-rest", "project:skill-server", "track-a"),
    ("task:skill-server-pipeline-migration", "project:skill-server", "track-b"),
]

# G3 single-child: todo→todo child_of → related_to
G3_TO_RELATED = [
    ("todo:cortex-boot-mustinline-invariant-guard", "todo:auto-inject-orchestrator-workflow-skill", "lever-b-followup"),
    ("todo:friction-13633-option-d-durable-identity", "todo:card-v0-ranking-current-status-slot", "option-slice"),
    ("todo:cortex-store-test-golden-drift", "todo:cortex-store-test-harness-fixture-drift", "follow-up"),
    ("todo:dispatch-on-behalf-brevity-advisory", "todo:dispatch-on-behalf-auto-sidecar", "phase-2-advisory"),
    ("todo:supersede-validation-flip-hard422", "todo:supersede-validate-assertion-parity", "follow-up"),
]

# G3 rewire sub-todos onto existing task container
G3_TO_EXISTING_TASK = [
    (
        "task:cursor-sdk-generate-peer",
        [
            "todo:cursor-sdk-shared-onbehalf-delivery",
            "todo:split-sdk-worker-failed-signal",
            "todo:cursor-sdk-heartbeat-tool-count",
        ],
    ),
]

# G3 arc promotion: parent todo slug → task with these leaf todos (flatten nested)
G3_ARC_PROMOTIONS: list[tuple[str, list[str]]] = [
    (
        "cortex-provenance-substrate-spec",
        [
            "cortex-provenance-discipline-skill",
            "entity-backed-claim-provenance-implementation",
            "provenance-spec-phase-1-schema-registration",
        ],
    ),
    (
        "cursor-sdk-closeout-dirty-baseline-under-capture",
        [
            "closeout-fs-shell-write-invisibility",
            "closeout-nonporcelain-honest-reporting",
            "cursor-sdk-closeout-rc3-edge-hardening",
        ],
    ),
    (
        "cursor-sdk-concurrent-closeout-metadata-contamination",
        [
            "cursor-sdk-closeout-metadata-slice-a",
            "cursor-sdk-closeout-serialize-all-writers",
        ],
    ),
    (
        "cursor-sdk-consolidation-orchestrator-contract",
        [
            "cursor-sdk-closeout-errno36-path-overcapture",
            "cursor-sdk-thread-reuse-hardening",
        ],
    ),
    (
        "investigate-anthropic-api-failure-modes-2026-05-16",
        [
            "reaudit-anthropic-adapter-against-current-mirror",
            "universal-max-tokens-model-ceiling-default",
            "workload-discipline-prompt-budgeting-note",
        ],
    ),
    (
        "openapi-cortex-api-remaining-routers-drift",
        [
            "openapi-cortex-api-drift-boot-subpackage",
            "openapi-cortex-api-drift-chunks",
            "openapi-cortex-api-drift-deadlines-gap",
            "openapi-cortex-api-drift-dispatch",
            "openapi-cortex-api-drift-documents",
            "openapi-cortex-api-drift-edges",
            "openapi-cortex-api-drift-entity-status",
            "openapi-cortex-api-drift-extraction-runs",
            "openapi-cortex-api-drift-gated",
            "openapi-cortex-api-drift-graph",
            "openapi-cortex-api-drift-ingest",
            "openapi-cortex-api-drift-reaper",
            "openapi-cortex-api-drift-reflective-journal",
            "openapi-cortex-api-drift-relationships",
            "openapi-cortex-api-drift-resolve",
            "openapi-cortex-api-drift-salience",
            "openapi-cortex-api-drift-session-journals-gap",
            "openapi-cortex-api-drift-staging",
            "openapi-cortex-api-drift-stats",
            "openapi-cortex-api-drift-surface-forms",
            "openapi-cortex-api-drift-tags",
            "openapi-cortex-api-drift-todo-audit",
            "openapi-cortex-api-drift-todo-retrieval",
            "openapi-cortex-api-drift-triage",
        ],
    ),
]


def _err(result: dict[str, Any], ctx: str) -> None:
    if "error" in result:
        raise RuntimeError(f"{ctx}: {result}")


def _load_entity(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
    if not row:
        raise RuntimeError(f"missing entity {entity_id}")
    return dict(row)


def _active_child_of(conn: sqlite3.Connection, source: str, target: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM relationships WHERE active=1 AND type='child_of' "
        "AND from_entity=? AND to_entity=?",
        (source, target),
    ).fetchone()
    return int(row["id"]) if row else None


def _active_rel(
    conn: sqlite3.Connection, source: str, target: str, type_id: str
) -> int | None:
    row = conn.execute(
        "SELECT id FROM relationships WHERE active=1 AND type=? "
        "AND from_entity=? AND to_entity=?",
        (type_id, source, target),
    ).fetchone()
    return int(row["id"]) if row else None


def _delete_g3_edge(conn: sqlite3.Connection, child: str, parent: str) -> bool:
    rel_id = _active_child_of(conn, child, parent)
    if rel_id is None:
        return False
    _err(
        execute_op("relationship_delete", {"relationship_id": rel_id}),
        f"delete G3 {child} -> {parent}",
    )
    return True


def _ensure_related_to(source: str, target: str, role: str) -> None:
    conn = _ro_conn()
    if _active_rel(conn, source, target, "related_to"):
        return
    _err(
        execute_op(
            "relationship_create",
            {
                "source_id": source,
                "target_id": target,
                "type_id": "related_to",
                "role": role,
                "agent": AGENT,
                "session_id": SESSION,
                "evidence": "G3 remediation: todo→todo child_of replaced with related_to grouping",
            },
        ),
        f"related_to {source} -> {target}",
    )


def _task_safe_attrs(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {k: v for k, v in parsed.items() if k not in _IMPLEMENT_ATTRS}


def _mirror_parent_membership(task_id: str, parent_id: str) -> None:
    conn = _ro_conn()
    for row in conn.execute(
        "SELECT to_entity, role FROM relationships WHERE active=1 AND type='child_of' "
        "AND from_entity=? AND to_entity NOT LIKE 'todo:%'",
        (parent_id,),
    ):
        target = row["to_entity"]
        role = row["role"] or "umbrella_project"
        if target.startswith("project:"):
            _ensure_related_to(task_id, target, role)
        else:
            _ensure_child_of(task_id, target, row["role"])


def _ensure_child_of(source: str, target: str, role: str | None = None) -> None:
    conn = _ro_conn()
    if _active_child_of(conn, source, target):
        return
    payload: dict[str, Any] = {
        "source_id": source,
        "target_id": target,
        "type_id": "child_of",
        "agent": AGENT,
        "session_id": SESSION,
        "evidence": "G3 remediation: leaf todo regrouped under task container",
    }
    if role:
        payload["role"] = role
    _err(execute_op("relationship_create", payload), f"child_of {source} -> {target}")


def _remediate_a2() -> int:
    conn = _ro_conn()
    changed = 0
    known = {(t, p): role for t, p, role in A2_RELATED_TO}
    rows = conn.execute(
        "SELECT id, from_entity, to_entity FROM relationships "
        "WHERE active=1 AND type='child_of' "
        "AND from_entity LIKE 'task:%' AND to_entity LIKE 'project:%'"
    ).fetchall()
    for row in rows:
        task_id, project_id = row["from_entity"], row["to_entity"]
        role = known.get((task_id, project_id), "umbrella_project")
        _err(
            execute_op("relationship_delete", {"relationship_id": int(row["id"])}),
            f"delete A2 {task_id} -> {project_id}",
        )
        _ensure_related_to(task_id, project_id, role)
        changed += 1
    return changed


def _remediate_g3_related() -> int:
    conn = _ro_conn()
    changed = 0
    for child, parent, role in G3_TO_RELATED:
        if _delete_g3_edge(conn, child, parent):
            changed += 1
        _ensure_related_to(child, parent, role)
    return changed


def _remediate_g3_existing_task() -> int:
    conn = _ro_conn()
    changed = 0
    for task_id, children in G3_TO_EXISTING_TASK:
        for child in children:
            # delete any todo→todo G3 edge involving this child
            rows = conn.execute(
                "SELECT id, to_entity FROM relationships WHERE active=1 AND type='child_of' "
                "AND from_entity=? AND to_entity LIKE 'todo:%'",
                (child,),
            ).fetchall()
            for row in rows:
                _err(
                    execute_op("relationship_delete", {"relationship_id": int(row["id"])}),
                    f"delete G3 {child} -> {row['to_entity']}",
                )
                changed += 1
            _ensure_child_of(child, task_id)
    return changed


def _promote_arc(slug: str, child_slugs: list[str]) -> None:
    todo_id = f"todo:{slug}"
    task_id = f"task:{slug}"
    conn = _ro_conn()

    parent_row = _load_entity(conn, todo_id)
    if not conn.execute("SELECT id FROM entities WHERE id=?", (task_id,)).fetchone():
        attrs = _task_safe_attrs(parent_row.get("attributes"))
        attrs["promoted_from_todo"] = todo_id
        _err(
            execute_op(
                "entity_create",
                {
                    "id": task_id,
                    "type": "task",
                    "name": parent_row["name"],
                    "description": parent_row.get("description") or "",
                    "attributes": attrs,
                    "source_uri": parent_row.get("source_uri"),
                },
            ),
            f"create {task_id}",
        )

    _mirror_parent_membership(task_id, todo_id)

    for child_slug in child_slugs:
        child_id = f"todo:{child_slug}"
        # remove all todo→todo child_of from this leaf
        for edge in conn.execute(
            "SELECT id, to_entity FROM relationships WHERE active=1 AND type='child_of' "
            "AND from_entity=? AND to_entity LIKE 'todo:%'",
            (child_id,),
        ):
            _err(
                execute_op("relationship_delete", {"relationship_id": int(edge["id"])}),
                f"delete G3 {child_id} -> {edge['to_entity']}",
            )
        _ensure_child_of(child_id, task_id)

    _ensure_related_to(todo_id, task_id, "container_promoted_to_task")
    stamp_attrs = _task_safe_attrs(parent_row.get("attributes"))
    stamp_attrs["promoted_to_task"] = task_id
    stamp_attrs["container_role"] = "retired_promoted_to_task"
    _err(
        execute_op(
            "entity_update",
            {
                "entity_id": todo_id,
                "attributes": stamp_attrs,
                "notes": (
                    f"Container role promoted to {task_id} during G3 structural remediation "
                    f"({SESSION})."
                ),
            },
        ),
        f"stamp promoted_to_task on {todo_id}",
    )


def _count_g3_a2() -> tuple[int, int]:
    conn = _ro_conn()
    g3 = [
        f
        for f in detect_entity_vocabulary_grammar(conn)
        if "G3" in f.get("detail", "")
    ]
    a2 = detect_entity_structural_antipattern(conn)
    return len(g3), len(a2)


def main() -> int:
    dry = "--dry-run" in sys.argv
    if dry:
        g3, a2 = _count_g3_a2()
        print(f"baseline G3={g3} A2={a2}")
        return 0

    a2_n = _remediate_a2()
    rel_n = _remediate_g3_related()
    task_n = _remediate_g3_existing_task()
    for slug, children in G3_ARC_PROMOTIONS:
        _promote_arc(slug, children)

    g3, a2 = _count_g3_a2()
    print(
        json.dumps(
            {
                "a2_remediated": a2_n,
                "g3_related_remediated": rel_n,
                "g3_existing_task_remediated_edges": task_n,
                "arcs_promoted": len(G3_ARC_PROMOTIONS),
                "remaining_g3": g3,
                "remaining_a2": a2,
            },
            indent=2,
        )
    )
    return 0 if g3 == 0 and a2 == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
