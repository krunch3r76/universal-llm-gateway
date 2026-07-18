"""Pure evaluator for todo-sourced implement admission (declared-state gate)."""

from __future__ import annotations

from dataclasses import dataclass

from implement_admission.dense_spec_schema import (
    DENSE_SPEC_RE,
    dense_spec_hash_uri,
    spec_basename,
    validate_dense_spec,
)
from implement_admission.density_triage_gate import (
    JUDGMENT_REQUIRED,
    MECHANICAL,
    RECON_PENDING,
    format_implement_triage_unknown_reason,
)


@dataclass(frozen=True, slots=True)
class ImplementReadyVerdict:
    admitted: bool
    code: str | None = None
    reason: str | None = None
    assertion_id: int | None = None


def _assertion_cites_dense_spec(
    evidence_uris: list[str] | None,
    *,
    source_uri: str | None,
) -> bool:
    if not evidence_uris:
        return False
    cited_matches = [u for u in evidence_uris if DENSE_SPEC_RE.search(u)]
    if not cited_matches:
        return False
    source_base = spec_basename(source_uri or "")
    if source_base is None:
        return True
    return any(spec_basename(u) == source_base for u in cited_matches)


def _assertion_inactive(assertion: dict, *, now_iso: str) -> bool:
    if assertion.get("superseded_by"):
        return True
    valid_until = assertion.get("valid_until")
    if valid_until and str(valid_until) <= now_iso:
        return True
    return False


def assertion_active(assertion: dict, *, now_iso: str) -> bool:
    return not _assertion_inactive(assertion, now_iso=now_iso)


def _reject(code: str, reason: str) -> ImplementReadyVerdict:
    return ImplementReadyVerdict(admitted=False, code=code, reason=reason)


def _skeptic_evidence_reject(
    *,
    todo_id: str,
    evidence_grounded: bool | None,
    evidence_unresolved: list[str] | None,
    evidence_mode: str | None,
) -> ImplementReadyVerdict | None:
    if evidence_grounded is not False:
        return None
    if evidence_mode == "stamp_missing":
        return _reject(
            "skeptic_evidence_stamp_missing",
            f"{todo_id}: skeptic ratification cites an agent-bus turn that "
            "could not be read — re-run the axis-2 skeptic pass",
        )
    if evidence_mode == "malformed":
        return _reject(
            "skeptic_evidence_malformed",
            f"{todo_id}: skeptic FILE_EVIDENCE_PATHS contains a malformed "
            "file-schemed entry — fix the typo and re-ratify",
        )
    if evidence_unresolved:
        joined = ", ".join(evidence_unresolved)
        return _reject(
            "skeptic_evidence_unresolved",
            f"{todo_id}: skeptic cited path(s) do not resolve: {joined}",
        )
    return _reject(
        "skeptic_evidence_missing",
        f"{todo_id}: skeptic reply lacks FILE_EVIDENCE_PATHS on a "
        "file-grounding-required workflow — cite resolved artifacts or "
        "re-run the axis-2 skeptic pass",
    )


def _acceptance_unpopulated_or_default(
    acs: list[str] | None,
    *,
    todo_id: str,
    name: str | None,
) -> bool:
    if not acs or not all(isinstance(x, str) and x.strip() for x in acs):
        return True
    defaults = {f"Complete work for {todo_id}"}
    if name:
        defaults.add(f"Complete {name}")
    return all(a in defaults for a in acs)


def evaluate_implement_ready(
    *,
    todo_id: str,
    density_triage: str | None,
    source_uri: str | None,
    implement_ready_assertion_id: int | None,
    assertion: dict | None,
    now_iso: str,
    dense_spec_uri: str | None = None,
    dense_spec_text: str | None = None,
    files_expected: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    entity_name: str | None = None,
    skeptic_ratified: bool = False,
    recon_waived: bool = False,
    check_requested: bool = False,
    skeptic_evidence_grounded: bool | None = None,
    skeptic_evidence_unresolved: list[str] | None = None,
    skeptic_evidence_mode: str | None = None,
    skeptic_unratified_reason: str | None = None,
) -> ImplementReadyVerdict:
    """Deterministic implement-readiness verdict over declared todo state."""
    triage = (density_triage or "").strip() or None
    if triage == MECHANICAL:
        return ImplementReadyVerdict(admitted=True)

    if triage == RECON_PENDING:
        return _reject(
            "implement_blocked_recon_pending",
            f"{todo_id}: recon not complete — run the two-axis recon and "
            "re-triage to judgment_required or mechanical before implement dispatch",
        )

    if triage != JUDGMENT_REQUIRED:
        return _reject(
            "implement_triage_unknown",
            format_implement_triage_unknown_reason(todo_id, density_triage),
        )

    if implement_ready_assertion_id is None:
        return _reject(
            "implement_not_ready_judgment_required",
            f"{todo_id}: judgment_required but implement_ready_assertion_id is "
            "absent — record an implement-ready assertion citing the dense spec",
        )

    if assertion is None:
        return _reject(
            "implement_ready_assertion_missing",
            f"{todo_id}: implement_ready_assertion_id={implement_ready_assertion_id} "
            "does not resolve to an assertion row",
        )

    if assertion.get("entity_id") != todo_id:
        return _reject(
            "implement_ready_assertion_entity_mismatch",
            f"{todo_id}: assertion {implement_ready_assertion_id} is bound to "
            f"{assertion.get('entity_id')!r}, not this todo",
        )

    if _assertion_inactive(assertion, now_iso=now_iso):
        return _reject(
            "implement_ready_assertion_inactive",
            f"{todo_id}: assertion {implement_ready_assertion_id} is superseded "
            "or expired — record a fresh implement-ready declaration",
        )

    dense_uri = (source_uri or "").strip()
    if not dense_uri:
        return _reject(
            "implement_not_ready_no_dense_spec",
            f"{todo_id}: source_uri must point at tasks/specs/{{slug}}.md or "
            "notes/system/specs/{slug}.md before implement dispatch",
        )

    evidence = assertion.get("evidence_uris")
    if not isinstance(evidence, list):
        evidence = None
    if not _assertion_cites_dense_spec(evidence, source_uri=dense_uri):
        return _reject(
            "implement_ready_assertion_spec_uncited",
            f"{todo_id}: assertion {implement_ready_assertion_id} must cite "
            f"the dense spec ({dense_uri}) in evidence_uris",
        )

    if dense_spec_text is None:
        return _reject(
            "implement_spec_unreadable",
            f"{todo_id}: dense spec at {dense_spec_uri or dense_uri} could not "
            "be read for schema validation",
        )
    schema = validate_dense_spec(dense_spec_text)
    if not schema.passed:
        return _reject(
            "implement_spec_not_dense",
            f"{todo_id}: {dense_spec_uri or dense_uri} fails dense-spec schema "
            f"({schema.code}: {schema.reason})",
        )
    if dense_spec_hash_uri(dense_spec_text) not in (evidence or []):
        return _reject(
            "implement_spec_drifted_since_ready",
            f"{todo_id}: current spec content is not attested by assertion "
            f"{implement_ready_assertion_id} (cite spec_sha256:<hex> of the "
            "validated content; rerun the validator and refresh the assertion)",
        )

    if not files_expected:
        return _reject(
            "implement_attrs_unpopulated",
            f"{todo_id}: implement-ready but attrs.files_expected is empty — "
            "distill files_expected from the dense spec at Gate-2 close "
            "(consult-routing densify lane).",
        )
    if _acceptance_unpopulated_or_default(
        acceptance_criteria,
        todo_id=todo_id,
        name=entity_name,
    ):
        return _reject(
            "implement_attrs_unpopulated",
            f"{todo_id}: attrs.acceptance_criteria is empty or the default "
            "placeholder — distill acceptance_criteria from the dense spec at "
            "Gate-2 close.",
        )

    # Axis-2 / Gate-6 is opt-in (attrs.check_requested=true). Default admit on
    # dense implement_ready alone; waiver/skeptic still satisfy when present.
    if check_requested and not skeptic_ratified and not recon_waived:
        reason = (
            f"{todo_id}: check_requested=true — axis-2 ratification required "
            "before implement — record a confirmed "
            f"status({todo_id}, skeptic_ratified, current) assertion citing the "
            "skeptic/panel thread AND the spec_sha256:<hex> URI of the current "
            "dense-spec content in evidence_uris, or set "
            "attributes.gate6_ratification_uri=agent-bus:{tid}#turn-N on the "
            "todo (with that turn carrying an affirmative verdict, the same "
            "spec_sha256 token, and resolvable FILE_EVIDENCE_PATHS), or set a "
            "hash-matched attributes.recon_waived JSON (run the axis-2 skeptic "
            "pass per cheap-recon-before-escalation)."
        )
        if skeptic_unratified_reason:
            reason += f" Unmet subcondition: {skeptic_unratified_reason}"
        return _reject("skeptic_pass_missing", reason)

    if check_requested and skeptic_ratified and not recon_waived:
        evidence_reject = _skeptic_evidence_reject(
            todo_id=todo_id,
            evidence_grounded=skeptic_evidence_grounded,
            evidence_unresolved=skeptic_evidence_unresolved,
            evidence_mode=skeptic_evidence_mode,
        )
        if evidence_reject is not None:
            return evidence_reject

    return ImplementReadyVerdict(
        admitted=True,
        assertion_id=implement_ready_assertion_id,
    )


__all__ = ["ImplementReadyVerdict", "assertion_active", "evaluate_implement_ready"]
