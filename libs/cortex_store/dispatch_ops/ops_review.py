"""Cortex review protocols — Layer 3 (Phase 2.0, cortex-graph-projection-and-audit-primitives).

Provides:
  _op_session_audit  — user-callable: audit entities touched in a session
  _op_case_audit     — user-callable: full audit (graph + fs) for a case
  _op_fill_gaps      — advisory: map findings to suggested cortex ops

Gate functions (_run_session_audit_graph_only, _run_session_audit_or_block) live in
ops_review_gate.py (split for [quality:sloc] ≤300 per-file invariant).

CORTEX_SESSION_AUDIT_MODE controls _op_session_close behavior:
  warn  (default) — findings emit cortex.session.audit.gaps.observed; close succeeds
                    with _warning in response.
  block (Phase 2.1) — critical gaps with no defer_gaps cause structured-error return
                      before any file I/O or DB mutation.

See v2 plan §1 for WARN→BLOCK transition protocol (≥7 days observation, env-only flip).
"""

from __future__ import annotations

import os
import time
from typing import Any

from ._shared import record
from .ops_audit_detectors import GRAPH_ONLY_KINDS, run_detectors
from .ops_review_gate import _run_session_audit_graph_only

_AUDIT_MODE_ENV = "CORTEX_SESSION_AUDIT_MODE"

# Gap kinds that warrant suggested cortex ops — advisory fills for _op_fill_gaps.
_GAP_FILL_ADVICE: dict[str, dict[str, str]] = {
    "entity_empty_description": {
        "op": "entity_update",
        "remedy": "Add a description field to the entity via entity_update.",
    },
    "entity_source_uri_missing": {
        "op": "entity_update",
        "remedy": "Set source_uri to the canonical file path for this entity.",
    },
    "case_no_assertions": {
        "op": "assert",
        "remedy": "Seed at least one assertion on the case entity.",
    },
    "case_no_relationships": {
        "op": "relationship_create",
        "remedy": "Create a relationship linking this case to a related entity.",
    },
    "case_no_documents": {
        "op": "relationship_create",
        "remedy": "Create an evidence_for relationship linking a document entity to this case.",
    },
    "document_not_wired_to_case": {
        "op": "relationship_create",
        "remedy": "Create an evidence_for relationship from the document to its case.",
    },
    "dangling_relationship_target": {
        "op": "entity_create",
        "remedy": "Create the missing target entity or update the relationship to point to an existing one.",
    },
    "dangling_attribute_reference": {
        "op": "entity_update",
        "remedy": "Update the attribute to reference an existing entity ID.",
    },
    "case_attribute_skill_dangling": {
        "op": "register_skill_substrate",
        "remedy": "Register the referenced skill via register_skill_substrate.",
    },
    "agent_skill_not_in_canonical_sandbox": {
        "op": "entity_update",
        "remedy": "Move the skill file to agent-skills/ and update source_uri.",
    },
    "entity_source_uri_unresolved": {
        "op": "entity_update",
        "remedy": "Correct source_uri to a resolvable path under the cortex files root.",
    },
    "marker_nesting_violation": {
        "op": "fs",
        "remedy": "Remove nested CORTEX_GENERATED markers from the document.",
    },
    "unregistered_document_in_markdown": {
        "op": "relationship_create",
        "remedy": "Create a document entity and wire it to the case via relationship_create.",
    },
    "markdown_section_drift": {
        "op": "fs",
        "remedy": "Use fs MCP to edit the document and sync the CORTEX_GENERATED block manually.",
    },
    "case_marker_absent": {
        "op": "fs",
        "remedy": "Use fs MCP to add the CORTEX_GENERATED marker block to the case document.",
    },
}


def _op_session_audit(
    session_id: str | None = None,
    entity_ids: list[str] | None = None,
    defer_gaps: dict[str, str] | None = None,
    **_: object,
) -> dict[str, Any]:
    """Manually invoke the session audit for a session ID.

    Runs graph-only detectors scoped to entity_ids (or full graph if empty).
    Does not block or modify session state — advisory only.
    Use case_audit for a full (graph + fs) audit of a case entity.
    """
    if not session_id:
        return {"error": "session_id is required"}

    ids = entity_ids or []
    start = time.time()
    findings = _run_session_audit_graph_only(session_id, ids)
    duration_ms = int((time.time() - start) * 1000)

    deferred_kinds = set(defer_gaps or {})
    criticals = [
        f
        for f in findings
        if f["severity"] == "critical" and f["kind"] not in deferred_kinds
    ]
    warnings = [f for f in findings if f["severity"] == "warning"]
    infos = [f for f in findings if f["severity"] == "info"]

    record(
        "cortex.audit.completed",
        subject=session_id,
        gap_count=len(findings),
        criticals=len(criticals),
        warnings=len(warnings),
        infos=len(infos),
        kinds_run=list(GRAPH_ONLY_KINDS),
        duration_ms=duration_ms,
        include_filesystem=False,
    )

    return {
        "session_id": session_id,
        "findings": findings,
        "gap_count": len(findings),
        "criticals": len(criticals),
        "warnings": len(warnings),
        "infos": len(infos),
        "duration_ms": duration_ms,
        "mode": os.environ.get(_AUDIT_MODE_ENV, "warn"),
    }


def _op_case_audit(
    subject: str | None = None,
    include_filesystem: bool = True,
    **_: object,
) -> dict[str, Any]:
    """Full audit for a case entity — graph-only + fs-touching detectors by default.

    Manual invocation path: includes filesystem detectors (include_filesystem=True
    default) since the caller expects a wait and needs the complete gap profile.
    Set include_filesystem=False to scope to graph-only if speed matters.
    """
    if not subject:
        return {"error": "subject is required (e.g. 'case:hei-flintridge-2026')"}

    start = time.time()
    findings = run_detectors(subject=subject, include_filesystem=include_filesystem)
    duration_ms = int((time.time() - start) * 1000)

    criticals = [f for f in findings if f["severity"] == "critical"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    infos = [f for f in findings if f["severity"] == "info"]

    record(
        "cortex.audit.completed",
        subject=subject,
        gap_count=len(findings),
        criticals=len(criticals),
        warnings=len(warnings),
        infos=len(infos),
        kinds_run="all",
        duration_ms=duration_ms,
        include_filesystem=include_filesystem,
    )
    for f in findings:
        record(
            "cortex.audit.gap.detected",
            kind=f["kind"],
            subject=f["subject"],
            severity=f["severity"],
            detail=f["detail"],
            audit_id=f["audit_id"],
        )

    return {
        "subject": subject,
        "findings": findings,
        "gap_count": len(findings),
        "criticals": len(criticals),
        "warnings": len(warnings),
        "infos": len(infos),
        "duration_ms": duration_ms,
        "_next": "use fill_gaps to get suggested remedies for each finding",
    }


def _op_fill_gaps(
    findings: list[dict[str, Any]] | None = None,
    subject: str | None = None,
    include_filesystem: bool = False,
    **_: object,
) -> dict[str, Any]:
    """Return suggested fills for audit findings.

    Accepts a findings list (from audit/case_audit/session_audit) or a subject
    to re-run case_audit and generate advice. Advisory only — does not modify state.

    include_filesystem defaults to False — fast advisory path. Pass True to include
    filesystem detectors before generating suggestions.
    """
    if not findings and subject:
        audit_result = _op_case_audit(
            subject=subject, include_filesystem=include_filesystem
        )
        if "error" in audit_result:
            return audit_result
        findings = audit_result.get("findings", [])

    if not findings:
        return {"error": "findings list or subject is required"}

    suggestions = []
    for f in findings:
        advice = _GAP_FILL_ADVICE.get(f["kind"])
        suggestions.append(
            {
                "kind": f["kind"],
                "subject": f["subject"],
                "severity": f["severity"],
                "detail": f["detail"],
                "audit_id": f["audit_id"],
                "suggested_op": advice["op"] if advice else "entity_update",
                "remedy": advice["remedy"]
                if advice
                else f"Review and repair the {f['kind']} gap manually.",
            }
        )

    return {
        "suggestions": suggestions,
        "count": len(suggestions),
        "_next": "Apply suggested ops to close each gap, then re-run case_audit to verify.",
    }


__all__ = [
    "_op_session_audit",
    "_op_case_audit",
    "_op_fill_gaps",
]
