"""Cortex audit detectors — public taxonomy + registry + runner.

Phase 1b of cortex-graph-projection-and-audit-primitives (v2 plan at
``tmp/prompts/cortex-primitives/implementation-plan-v2.md``).

Detector implementations live under ``./_detectors/`` — themed modules so
each file stays well under the [quality:sloc] 400-line budget. Public
surface re-exported here:

- Taxonomy: ``GRAPH_ONLY_KINDS``, ``FS_TOUCHING_KINDS``, ``INFO_KINDS``,
  ``ALL_KINDS``, ``SEVERITY``
- Registry / runner: ``get_all_detectors``, ``run_detectors``

Findings shape: ``{kind, subject, severity, detail, audit_id}`` — see
``_detectors/_shared.py::_finding``.

Severity rationale per v2 plan §6. Graph-only set is the default for
``session_audit`` (target <100ms budget per W1); fs-touching set is opt-in
via ``include_filesystem=True``.
"""

from __future__ import annotations

from typing import Any

from ..db import cortex_conn
from ._detectors._shared import SEVERITY
from ._detectors.agent_skill import detect_agent_skill_related_skills_no_relationship
from ._detectors.auditor import (
    detect_confirmed_attribute_no_assertion,
    detect_confirmed_entity_no_assertions,
)
from ._detectors.cases import (
    detect_case_attribute_skill_dangling,
    detect_case_marker_absent,
    detect_case_no_assertions,
    detect_case_no_documents,
    detect_case_no_relationships,
    detect_document_not_wired_to_case,
)
from ._detectors.entity import (
    detect_agent_skill_not_in_canonical_sandbox,
    detect_entity_empty_description,
    detect_entity_source_uri_missing,
    detect_entity_source_uri_unresolved,
)
from ._detectors.fk_orphan import detect_foreign_key_orphan
from ._detectors.forbidden_surfaces import detect_forbidden_surfaces
from ._detectors.git_reconcile import detect_landed_claim_not_on_master
from ._detectors.implement_ready_spec import detect_implement_ready_spec_unvalidated
from ._detectors.markdown_render import (
    detect_markdown_section_drift,
    detect_marker_nesting_violation,
    detect_unregistered_document_in_markdown,
)
from ._detectors.panel_disposition import (
    detect_panel_disposition_incomplete,
    detect_panel_falsifier_phase3_metric,
)
from ._detectors.predicate_form import detect_unresolved_bare_token_in_predicate_form
from ._detectors.project import detect_project_required_skills_no_relationship
from ._detectors.provenance_staging import detect_provenance_cites_staging
from ._detectors.relationship import detect_dangling_relationship_target
from ._detectors.session import detect_prior_session_id_omitted
from ._detectors.skill_binding import (
    detect_skill_binding_missing,
    detect_skill_binding_tool_unknown,
)
from ._detectors.todo import (
    detect_todo_dense_spec_attributes_unpopulated,
    detect_todo_implementation_seed_incomplete,
)
from ._detectors.todo_density_risk import detect_todo_implement_readiness_risk
from ._detectors.workflow_coherence import (
    detect_decision_deprecated_not_terminal,
    detect_decision_workflow_state_incoherent,
)

# Gap taxonomy per v2 plan §6
GRAPH_ONLY_KINDS = {
    "dangling_attribute_reference",
    "dangling_relationship_target",
    "foreign_key_orphan",
    "entity_source_uri_missing",
    "entity_empty_description",
    "case_no_assertions",
    "case_no_relationships",
    "case_no_documents",
    "document_not_wired_to_case",
    "case_attribute_skill_dangling",
    "marker_nesting_violation",
    "prior_session_id_omitted",
    # Auditor-validatability gaps (Checks 4–5) — fire at session_close for
    # entities touched in the session. Advisory, never blocking.
    "confirmed_entity_no_assertions",
    "confirmed_attribute_no_assertion",
    # Panel disposition completeness + Phase-3 falsifier metric (thread 1206)
    "panel_disposition_incomplete",
    "panel_falsifier_phase3_metric",
    # Skill-manifest structural drift — see migrations 041 + 045 (covers
    # project / plan / plan_phase / todo).
    "project_required_skills_no_relationship",
    "agent_skill_related_skills_no_relationship",
    # v1.3.1 normalization ledger Path 2 detector
    "unresolved_bare_token_in_predicate_form",
    # skill_binding substrate (thread 1067)
    "skill_binding_missing",
    "skill_binding_tool_unknown",
    # entity-state coherence (thread 1116) — confirmed decision resting at
    # NULL/proposed. Parameterized engine; decision landed first.
    "decision_workflow_state_incoherent",
    # todo seed-contract completeness (thread 1144) — open/in_progress todos
    # missing source_uri, required_skills, or a non-skill context edge.
    "todo_implementation_seed_incomplete",
    "todo_dense_spec_attributes_unpopulated",
    "todo_implement_readiness_risk",
    # missing_handoff retired — handoffs are optional artifacts for manual
    # copy-paste at end of chat; absence is not a gap (assertion 8384,
    # session web-2026-05-04-1057).
}

FS_TOUCHING_KINDS = {
    "entity_source_uri_unresolved",
    "agent_skill_not_in_canonical_sandbox",
    "unregistered_document_in_markdown",
    "markdown_section_drift",
    "forbidden_surfaces",
    # Landed-claim-vs-master-ref reconciliation (thread 1153) — touches the
    # repo via the git-integration-worker route, so it runs in the opt-in
    # include_filesystem pass, off the <100ms graph-only session_audit budget.
    "landed_claim_not_on_master",
    # Staging provenance in durable docs / entity attrs (friction 20345).
    "provenance_cites_staging",
}

INFO_KINDS = {
    "case_marker_absent",
    "decision_deprecated_not_terminal",
    "panel_falsifier_phase3_metric",
}

ALL_KINDS = GRAPH_ONLY_KINDS | FS_TOUCHING_KINDS | INFO_KINDS


def get_all_detectors() -> dict[str, Any]:
    """Registry of all detectors per v2 plan §6. Graph-only run by default for session_audit."""
    return {
        "dangling_attribute_reference": lambda c, s=None: [],  # TODO: implement (attributes referencing missing entities)
        "dangling_relationship_target": detect_dangling_relationship_target,
        "foreign_key_orphan": detect_foreign_key_orphan,
        "entity_source_uri_missing": detect_entity_source_uri_missing,
        "entity_empty_description": detect_entity_empty_description,
        "case_no_assertions": detect_case_no_assertions,
        "case_no_relationships": detect_case_no_relationships,
        "case_no_documents": detect_case_no_documents,
        "document_not_wired_to_case": detect_document_not_wired_to_case,
        "case_attribute_skill_dangling": detect_case_attribute_skill_dangling,
        "marker_nesting_violation": detect_marker_nesting_violation,
        "prior_session_id_omitted": detect_prior_session_id_omitted,
        "confirmed_entity_no_assertions": detect_confirmed_entity_no_assertions,
        "confirmed_attribute_no_assertion": detect_confirmed_attribute_no_assertion,
        "panel_disposition_incomplete": detect_panel_disposition_incomplete,
        "panel_falsifier_phase3_metric": detect_panel_falsifier_phase3_metric,
        "project_required_skills_no_relationship": detect_project_required_skills_no_relationship,
        "agent_skill_related_skills_no_relationship": detect_agent_skill_related_skills_no_relationship,
        "entity_source_uri_unresolved": detect_entity_source_uri_unresolved,
        "agent_skill_not_in_canonical_sandbox": detect_agent_skill_not_in_canonical_sandbox,
        "unregistered_document_in_markdown": detect_unregistered_document_in_markdown,
        "markdown_section_drift": detect_markdown_section_drift,
        "forbidden_surfaces": detect_forbidden_surfaces,
        "case_marker_absent": detect_case_marker_absent,
        "unresolved_bare_token_in_predicate_form": detect_unresolved_bare_token_in_predicate_form,
        "skill_binding_missing": detect_skill_binding_missing,
        "skill_binding_tool_unknown": detect_skill_binding_tool_unknown,
        "decision_workflow_state_incoherent": detect_decision_workflow_state_incoherent,
        "decision_deprecated_not_terminal": detect_decision_deprecated_not_terminal,
        "todo_implementation_seed_incomplete": detect_todo_implementation_seed_incomplete,
        "todo_dense_spec_attributes_unpopulated": detect_todo_dense_spec_attributes_unpopulated,
        "todo_implement_readiness_risk": detect_todo_implement_readiness_risk,
        "landed_claim_not_on_master": detect_landed_claim_not_on_master,
        "provenance_cites_staging": detect_provenance_cites_staging,
        "implement_ready_spec_unvalidated": lambda c, s=None: [],  # DISABLED 2026-06-20: cortex_api audit context cannot read repo tasks/specs directly, so this false-positived every implement_ready (incl. the valid 20197). Template landed_claim_not_on_master routes repo reads via the git-worker; this read fs directly. Code + tests retained; needs repo-aware redesign (friction 20198 follow-up).
    }


def run_detectors(
    kinds: list[str] | None = None,
    subject: str | None = None,
    subjects: list[str] | None = None,
    include_filesystem: bool = False,
) -> list[dict[str, Any]]:
    """Run selected detectors. Graph-only by default (W1). include_filesystem=true adds fs ones.

    subjects: when non-empty, runs all subjects through each detector inside
    a single DB connection — avoids N open/close cycles per entity
    (W1 <100ms budget). Takes precedence over subject when provided.
    Deduplicates by audit_id.
    """
    with cortex_conn() as conn:
        detectors = get_all_detectors()
        if kinds is None:
            selected = list(GRAPH_ONLY_KINDS)
            selected.extend(list(INFO_KINDS))
            if include_filesystem:
                selected.extend(list(FS_TOUCHING_KINDS))
        else:
            selected = [k for k in kinds if k in ALL_KINDS]
        # A kind may belong to more than one taxonomy set (e.g.
        # panel_falsifier_phase3_metric ∈ GRAPH_ONLY_KINDS ∩ INFO_KINDS), and a
        # caller may pass duplicate kinds — dedup so each detector runs once on
        # the non-batched path (the subjects path already dedups by audit_id).
        selected = list(dict.fromkeys(selected))

        if subjects:
            # Batched multi-subject path — single DB connection, deduplicate by audit_id.
            seen: set[str] = set()
            findings: list[dict[str, Any]] = []
            for subj in subjects:
                for k in selected:
                    if k in detectors:
                        for f in detectors[k](conn, subj):
                            if f["audit_id"] not in seen:
                                seen.add(f["audit_id"])
                                findings.append(f)
            return findings

        findings = []
        for k in selected:
            if k in detectors:
                detector = detectors[k]
                findings.extend(detector(conn, subject))
        return findings


# Re-export detector functions so callers (e.g. tests) that import them
# from this module continue to work without changing their import paths.
__all__ = [
    "ALL_KINDS",
    "FS_TOUCHING_KINDS",
    "GRAPH_ONLY_KINDS",
    "INFO_KINDS",
    "SEVERITY",
    "detect_agent_skill_not_in_canonical_sandbox",
    "detect_case_attribute_skill_dangling",
    "detect_case_marker_absent",
    "detect_case_no_assertions",
    "detect_case_no_documents",
    "detect_case_no_relationships",
    "detect_confirmed_attribute_no_assertion",
    "detect_confirmed_entity_no_assertions",
    "detect_dangling_relationship_target",
    "detect_decision_deprecated_not_terminal",
    "detect_decision_workflow_state_incoherent",
    "detect_document_not_wired_to_case",
    "detect_entity_empty_description",
    "detect_entity_source_uri_missing",
    "detect_entity_source_uri_unresolved",
    "detect_forbidden_surfaces",
    "detect_implement_ready_spec_unvalidated",
    "detect_landed_claim_not_on_master",
    "detect_markdown_section_drift",
    "detect_marker_nesting_violation",
    "detect_prior_session_id_omitted",
    "detect_provenance_cites_staging",
    "detect_agent_skill_related_skills_no_relationship",
    "detect_project_required_skills_no_relationship",
    "detect_skill_binding_missing",
    "detect_skill_binding_tool_unknown",
    "detect_todo_dense_spec_attributes_unpopulated",
    "detect_todo_implementation_seed_incomplete",
    "detect_todo_implement_readiness_risk",
    "detect_unregistered_document_in_markdown",
    "get_all_detectors",
    "run_detectors",
]
