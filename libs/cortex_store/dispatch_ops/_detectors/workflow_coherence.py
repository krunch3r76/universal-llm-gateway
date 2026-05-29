"""Workflow-state coherence audit detectors.

An entity's ``status`` and ``workflow_state`` must not contradict: an adopted /
canonical entity (``status='confirmed'``) must not rest at a pre-adoption or
unset workflow_state. This is the third instance of the entity-state-coherence
failure mode (after ``skill_binding`` and arc-worktree); the principle is
codified in ``agent-skills/entity-lifecycle-discipline.md`` and the gate is
parameterized here so the same engine generalizes to any workflow-typed entity.

Landed for ``decision`` first (census 2026-05-29: 297 confirmed entities resting
at NULL/proposed). Mirrors the ``skill_binding_missing`` precedent: check the
entity column, enumerate flagged entities, emit findings, and the enumeration IS
the deterministic remediation set (re-audit-to-zero closeout after backfill).
"""

from __future__ import annotations

from typing import Any

from ...db import query
from ._shared import _finding

# Parameterization per the design: entity_type -> (adopted_status_set,
# pre_adoption_state_set). ``None`` in the pre-adoption set matches a NULL
# workflow_state (the pre-column / pre-default legacy cohort). Add a row here
# and register the matching kind to extend the gate to a new workflow-typed
# entity; the detection engine below is type-agnostic.
_COHERENCE_RULES: dict[str, tuple[tuple[str, ...], tuple[str | None, ...]]] = {
    "decision": (("confirmed",), (None, "proposed")),
}


def _detect_workflow_state_incoherent(
    conn,
    entity_type: str,
    adopted_status: tuple[str, ...],
    pre_adoption_states: tuple[str | None, ...],
    subject: str | None = None,
) -> list[dict[str, Any]]:
    """Flag adopted entities resting at a pre-adoption / unset workflow_state.

    suggested_target is ``superseded`` when the entity is the target of an
    active ``supersedes`` edge (retirement evidence), else ``accepted``.
    """
    status_ph = ",".join(["?"] * len(adopted_status))
    clauses = ["e.type = ?", f"e.status IN ({status_ph})"]
    params: list[Any] = [entity_type, *adopted_status]

    state_clauses: list[str] = []
    non_null = [s for s in pre_adoption_states if s is not None]
    if None in pre_adoption_states:
        state_clauses.append("e.workflow_state IS NULL")
    if non_null:
        ph = ",".join(["?"] * len(non_null))
        state_clauses.append(f"e.workflow_state IN ({ph})")
        params.extend(non_null)
    if not state_clauses:
        return []
    clauses.append("(" + " OR ".join(state_clauses) + ")")

    if subject:
        clauses.append("e.id = ?")
        params.append(subject)

    sql = (
        "SELECT e.id, e.workflow_state, "
        "CASE WHEN sup.to_entity IS NOT NULL THEN 'superseded' "
        "ELSE 'accepted' END AS suggested_target "
        "FROM entities e "
        "LEFT JOIN relationships sup "
        "ON sup.to_entity = e.id AND sup.type = 'supersedes' AND sup.active = 1 "
        f"WHERE {' AND '.join(clauses)} "
        "GROUP BY e.id"
    )
    rows = query(conn, sql, tuple(params))
    kind = f"{entity_type}_workflow_state_incoherent"
    return [
        _finding(
            kind,
            r["id"],
            f"{entity_type} status IN {adopted_status} but workflow_state="
            f"{r['workflow_state'] or 'NULL'!r} (pre-adoption/unset) — "
            f"advance to {r['suggested_target']!r}",
        )
        for r in rows
    ]


def detect_decision_workflow_state_incoherent(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """confirmed decisions resting at NULL/proposed workflow_state."""
    adopted_status, pre_states = _COHERENCE_RULES["decision"]
    return _detect_workflow_state_incoherent(
        conn, "decision", adopted_status, pre_states, subject
    )


def detect_decision_deprecated_not_terminal(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """INFO: deprecated decisions not resting in a registered terminal state.

    Terminal states are read from the ``workflow_schemas`` registry so the
    check stays data-driven. Degrades to no findings when the registry is
    absent or carries no terminal_states for the type.
    """
    reg = query(
        conn,
        "SELECT terminal_states FROM workflow_schemas WHERE entity_type = 'decision'",
    )
    if not reg or not reg[0].get("terminal_states"):
        return []
    import json

    try:
        terminals = json.loads(reg[0]["terminal_states"])
    except (TypeError, ValueError):
        return []
    if not isinstance(terminals, list) or not terminals:
        return []

    term_ph = ",".join(["?"] * len(terminals))
    clauses = [
        "type = 'decision'",
        "status = 'deprecated'",
        f"(workflow_state IS NULL OR workflow_state NOT IN ({term_ph}))",
    ]
    params: list[Any] = list(terminals)
    if subject:
        clauses.append("id = ?")
        params.append(subject)
    sql = f"SELECT id, workflow_state FROM entities WHERE {' AND '.join(clauses)}"
    rows = query(conn, sql, tuple(params))
    return [
        _finding(
            "decision_deprecated_not_terminal",
            r["id"],
            f"deprecated decision at workflow_state={r['workflow_state'] or 'NULL'!r} "
            f"— retire to a terminal state {terminals}",
        )
        for r in rows
    ]


__all__ = [
    "detect_decision_deprecated_not_terminal",
    "detect_decision_workflow_state_incoherent",
]
