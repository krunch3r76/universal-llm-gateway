"""Shared primitives for audit-detector modules.

Houses the kind→severity map, the `_finding` builder, and the identifier-shape
regex used by Check 5's value-substring gate. Co-located here so per-kind
detector modules can `from ._shared import _finding` without circular imports
back into ``ops_audit_detectors`` (which itself imports the detectors).
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# Severity per v2 plan §6. Kept alongside `_finding` so each detector module
# resolves a single import for the standard finding shape. The taxonomy
# *kind sets* (GRAPH_ONLY_KINDS, FS_TOUCHING_KINDS, INFO_KINDS) remain on the
# public surface in ``ops_audit_detectors`` — callers grep for those by name.
SEVERITY = {
    "dangling_attribute_reference": "critical",
    "dangling_relationship_target": "critical",
    "agent_skill_not_in_canonical_sandbox": "critical",
    "case_attribute_skill_dangling": "critical",
    "entity_source_uri_unresolved": "critical",
    "entity_empty_description": "warning",
    "entity_source_uri_missing": "warning",
    "case_no_assertions": "warning",
    "case_no_relationships": "warning",
    "case_no_documents": "warning",
    "document_not_wired_to_case": "warning",
    "unregistered_document_in_markdown": "warning",
    "markdown_section_drift": "warning",
    "marker_nesting_violation": "warning",
    "prior_session_id_omitted": "warning",
    # Auditor-validatability gaps — warning severity (advisory, never critical)
    "confirmed_entity_no_assertions": "warning",
    "confirmed_attribute_no_assertion": "warning",
    "case_marker_absent": "info",
    # Skill-manifest structural drift (migrations 041 + 045). project /
    # plan / plan_phase / todo entities whose required_skills attribute and
    # `requires` relationships to the named agent_skill entities drift apart
    # (in either direction).
    "project_required_skills_no_relationship": "warning",
    # v1.3.1 normalization-decision ledger (shadow, Path 2/3)
    "unresolved_bare_token_in_predicate_form": "warning",
    # skill_binding substrate (thread 1067 backfill, U2 audit-gate)
    "skill_binding_missing": "warning",
    "skill_binding_tool_unknown": "warning",
    "forbidden_surfaces": "warning",
    # entity-state coherence — adopted entity resting at pre-adoption/unset
    # workflow_state (thread 1116; entity-lifecycle-discipline). Parameterized
    # engine, decision landed first.
    "decision_workflow_state_incoherent": "warning",
    "decision_deprecated_not_terminal": "info",
    # Todo seed-contract completeness (thread 1144; decision:todo-creation-rich-seed-contract).
    # Fires on open/in_progress todos missing source_uri, required_skills, or a
    # context edge to a non-agent_skill entity.
    "todo_implementation_seed_incomplete": "warning",
}

# Identifier-shaped attribute value: alphanumerics + ``-._:/`` only. Used by
# detect_confirmed_attribute_no_assertion to gate substring matching on
# values (free-text values are skipped — too noisy for substring detection).
_IDENT_SHAPED_VALUE_RE = re.compile(r"^[\w\-.:/]+$")


def _finding(
    kind: str, subject: str, detail: str, audit_id: str | None = None
) -> dict[str, Any]:
    """Standard finding shape. audit_id for correlation across runs."""
    severity = SEVERITY.get(kind, "warning")
    if not audit_id:
        audit_id = hashlib.md5(f"{kind}:{subject}".encode()).hexdigest()[:12]
    return {
        "kind": kind,
        "subject": subject,
        "severity": severity,
        "detail": detail,
        "audit_id": audit_id,
    }


__all__ = ["SEVERITY", "_IDENT_SHAPED_VALUE_RE", "_finding"]
