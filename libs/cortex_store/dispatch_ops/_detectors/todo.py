"""Todo seed-contract completeness gate.

Fires when an implementation-intent todo (workflow_state ∈ {open, in_progress})
is missing the structural fields that make it navigable by a fresh agent without
session memory:

  * source_uri — stub spec file (tasks/specs/{slug}.md) a fresh agent opens first
  * required_skills — ≥1 entry in attributes (agent_skill references for execution)
  * context edge — ≥1 active relationship incident to the todo whose *other*
    endpoint is NOT an agent_skill entity
    (e.g. references→decision:*, relates_to→service:*, evidence_uris→thread sidecar)

Suppressed when:
  * workflow_state ∈ {done, deferred, cancelled, blocked} — not implementation-intent
  * attributes.backlog = true — author explicitly marked this as backlog-only
  * attributes.seed_contract_ack present (any value) — documented-intent escape hatch

The context-edge predicate filters on the *other endpoint's* entity type (NOT an
agent_skill), not the relationship type. This is deliberate: a bare skill-edge
proves the executor read their skills, but does not supply the decision rationale or
substrate context a fresh agent needs to author a spec. Prose mentions in description
do not count — only graph-traversable relationships.

The edge query is direction-agnostic. Symmetric relationship types (e.g.
related_to) are stored lexicographically canonicalized, so a todo's context edge
to decision:* / service:* lands with the todo as *target* (decision:/service:
sort before todo:). Asymmetric types (child_of, references) preserve insertion
direction. Counting edges *incident* to the todo — either endpoint — covers both
cases without depending on canonicalization order.

Severity: warning. A gap is a discipline shortfall, never a critical fault.

Grounded in: decision:todo-creation-rich-seed-contract (thread 1144).
"""

from __future__ import annotations

import json
from typing import Any

from ...db import query
from ._shared import _finding

_IMPL_INTENT_STATES = ("open", "in_progress")


def detect_todo_implementation_seed_incomplete(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Todos in open/in_progress state missing source_uri, required_skills, or
    a context edge — the minimum structural seed for spec-without-session handoff.

    Does not flag todos outside implementation-intent states, or todos suppressed
    via attributes.backlog=true / attributes.seed_contract_ack.
    """
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

    # Filter suppressed todos; accumulate qualifying rows with parsed attrs.
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

    # Bulk-load context edges for qualifying IDs in one query.
    # A context edge is any active relationship incident to the todo (either
    # endpoint) whose *other* endpoint is NOT an agent_skill — skill-only edges
    # prove executor hygiene, not substrate context. Direction-agnostic because
    # symmetric types (related_to) canonicalize the todo into the target slot.
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
        # For each endpoint that is one of our todos, the OTHER endpoint must
        # not be an agent_skill for the edge to count as substrate context.
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


__all__ = ["detect_todo_implementation_seed_incomplete"]
