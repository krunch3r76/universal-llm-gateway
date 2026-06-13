"""Todo seed-contract completeness gate.

Fires when an implementation-intent todo (workflow_state ∈ {open, in_progress})
is missing structural fields that make it navigable by a fresh agent without
session memory. Checks are driven by the ``todo`` row in
``type_attribute_schemas`` when registered (migration 059); pre-migration
sandboxes with no registry row skip this detector (graceful degradation).

Default seed contract when the registry row exists:
  * source_uri — stub spec file (tasks/specs/{slug}.md) a fresh agent opens first
  * required_skills — when listed in the registry optional/required keys
  * context edge — ≥1 active relationship incident to the todo whose *other*
    endpoint is NOT an agent_skill entity

Suppressed when:
  * workflow_state ∈ {done, deferred, cancelled, blocked} — not implementation-intent
  * attributes.backlog = true — author explicitly marked this as backlog-only
  * attributes.seed_contract_ack present (any value) — documented-intent escape hatch

Grounded in: decision:todo-creation-rich-seed-contract (thread 1144);
tasks/specs/implement-input-schema.md §3 (registry convergence).
"""

from __future__ import annotations

import json
from typing import Any

from ...db import query
from ...type_schemas import type_attribute_schema
from ._shared import _finding

_IMPL_INTENT_STATES = ("open", "in_progress")


def detect_todo_implementation_seed_incomplete(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Todos in open/in_progress state missing registry-defined seed fields."""
    schema = type_attribute_schema(conn, "todo")
    if schema is None:
        return []

    registry_keys = set(schema["required"]) | set(schema["optional"])
    check_required_skills = "required_skills" in registry_keys

    placeholders = ",".join("?" * len(_IMPL_INTENT_STATES))
    sql = (
        "SELECT id, name, source_uri, attributes FROM entities "
        f"WHERE type = 'todo' AND workflow_state IN ({placeholders})"
    )
    params: tuple = tuple(_IMPL_INTENT_STATES)
    if subject:
        sql += " AND id = ?"
        params = (*params, subject)

    rows = query(conn, sql, params)

    impl_rows: list[dict] = []
    for r in rows:
        attrs = r.get("attributes")
        try:
            if isinstance(attrs, str):
                attrs = json.loads(attrs) if attrs else {}
        except json.JSONDecodeError:
            attrs = {}
        if not isinstance(attrs, dict):
            attrs = {}

        if attrs.get("backlog") is True or attrs.get("seed_contract_ack") is not None:
            continue

        impl_rows.append({**r, "_attrs": attrs})

    if not impl_rows:
        return []

    entity_ids = [r["id"] for r in impl_rows]
    entity_id_set = set(entity_ids)
    edge_placeholders = ",".join("?" * len(entity_ids))
    context_edge_rows = query(
        conn,
        f"SELECT from_entity, to_entity FROM relationships "
        f"WHERE active = 1 "
        f"AND (from_entity IN ({edge_placeholders}) "
        f"OR to_entity IN ({edge_placeholders}))",
        (*entity_ids, *entity_ids),
    )
    has_context_edge: set[str] = set()
    for edge in context_edge_rows:
        frm, to = edge["from_entity"], edge["to_entity"]
        if frm in entity_id_set and not to.startswith("agent_skill:"):
            has_context_edge.add(frm)
        if to in entity_id_set and not frm.startswith("agent_skill:"):
            has_context_edge.add(to)

    findings: list[dict[str, Any]] = []
    for r in impl_rows:
        todo_id = r["id"]
        attrs = r["_attrs"]
        gaps: list[str] = []

        if not r.get("source_uri") or not str(r["source_uri"]).strip():
            gaps.append("source_uri (stub spec at tasks/specs/{slug}.md)")

        if check_required_skills:
            rs = attrs.get("required_skills")
            if not rs or (isinstance(rs, list) and len(rs) == 0):
                gaps.append("required_skills (≥1 agent_skill slug in attributes)")

        if todo_id not in has_context_edge:
            gaps.append(
                "context edge (≥1 active relationship to decision:*, service:*, "
                "or thread sidecar — prose mentions do not count)"
            )

        if gaps:
            findings.append(
                _finding(
                    "todo_implementation_seed_incomplete",
                    todo_id,
                    f"todo '{r['name']}' missing required seed fields: "
                    f"{'; '.join(gaps)}. "
                    "Suppress via attributes.backlog=true (backlog-only) or "
                    "attributes.seed_contract_ack='<reason>' (documented intent). "
                    "See decision:todo-creation-rich-seed-contract.",
                )
            )

    return findings


def _attrs_list_nonempty(raw: object) -> bool:
    return isinstance(raw, list) and len(raw) > 0


def detect_todo_dense_spec_attributes_unpopulated(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Declared Gate-2-closed todos missing distilled implement-lane attributes."""
    placeholders = ",".join("?" * len(_IMPL_INTENT_STATES))
    sql = (
        "SELECT id, name, source_uri, attributes FROM entities "
        f"WHERE type = 'todo' AND workflow_state IN ({placeholders})"
    )
    params: tuple = tuple(_IMPL_INTENT_STATES)
    if subject:
        sql += " AND id = ?"
        params = (*params, subject)

    rows = query(conn, sql, params)
    findings: list[dict[str, Any]] = []

    for r in rows:
        attrs = r.get("attributes")
        try:
            if isinstance(attrs, str):
                attrs = json.loads(attrs) if attrs else {}
        except json.JSONDecodeError:
            attrs = {}
        if not isinstance(attrs, dict):
            attrs = {}

        if attrs.get("attributes_distillation_waived") is not None:
            continue
        if attrs.get("density_triage") != "judgment_required":
            continue
        if not r.get("source_uri") or not str(r["source_uri"]).strip():
            continue
        if attrs.get("implement_ready_assertion_id") is None:
            continue

        missing: list[str] = []
        if not _attrs_list_nonempty(attrs.get("files_expected")):
            missing.append("files_expected")
        if not _attrs_list_nonempty(attrs.get("acceptance_criteria")):
            missing.append("acceptance_criteria")
        if not missing:
            continue

        spec_path = str(r["source_uri"]).strip()
        findings.append(
            _finding(
                "todo_dense_spec_attributes_unpopulated",
                r["id"],
                f"todo '{r['name']}' is implement-ready (assertion "
                f"{attrs.get('implement_ready_assertion_id')}) but missing "
                f"distilled attributes: {', '.join(missing)}. Dense spec at "
                f"{spec_path}. Distill files_expected + acceptance_criteria "
                "from the dense spec at Gate-2 close (consult-routing densify "
                "lane). Suppress via attributes.attributes_distillation_waived="
                "'<reason>' when documented intent waives distillation.",
            )
        )

    return findings


__all__ = [
    "detect_todo_dense_spec_attributes_unpopulated",
    "detect_todo_implementation_seed_incomplete",
]
