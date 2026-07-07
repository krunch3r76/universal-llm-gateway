"""Implement-readiness gate + doc_validate attestation guard (side-effect locus).

The doc_validate attestation guard lives here at the dispatch-time side-effect
call site — NOT in evaluate_implement_ready / preflight_implement_ready (those
are re-evaluated by doc_validate and guarding there is circular).

Drift policy: attestation pins semantic template_version with skill_digest
tracked independently; template_sha256 is exact-drift detection only. A
non-breaking pedagogy/skill edit that bumps template_version must NOT
false-block a previously-attested spec when validate_dense_spec still PASSes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.doc_validate_attestation import (
    DocValidateAttestation,
    DocValidateAttestationVerdict,
    doc_validate_attestation_tokens,
    evaluate_doc_validate_attestation,
    extract_doc_validate_attestation,
)
from implement_admission.implement_ready import evaluate_implement_ready
from implement_admission.implement_ready_gate_resolve import (
    ImplementReadyCortex,
    SkepticRatificationOutcome,
    coerce_assertion_id,
    decode_gate_attributes,
    pin_needs_resolution,
    read_dense_spec_text,
    resolve_fresh_implement_ready,
    resolve_skeptic_ratification,
    select_cited_dense_spec_uri,
)
from implement_admission.recon_waiver import parse_recon_waiver, recon_waived_bool
from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import SourceKind

_DEFAULT_DOC_TYPE = "implement_dense_spec"


class ImplementReadyGateError(Exception):
    """422 implement admission failure surfaced by require_implement_ready."""

    def __init__(
        self,
        *,
        request_id: str,
        field: str,
        reason: str,
        code: str,
        status_code: int = 422,
    ) -> None:
        self.request_id = request_id
        self.field = field
        self.reason = reason
        self.code = code
        self.status_code = status_code
        super().__init__(reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.reason,
            "code": self.code,
            "field": self.field,
            "request_id": self.request_id,
            "status_code": self.status_code,
        }


def require_implement_ready(
    *,
    request_id: str,
    source_ref: str | None,
    cortex: ImplementReadyCortex,
    workspaces_root: Any | None = None,
    skip_doc_validate_guard: bool = False,
    resolve_skeptic: Any | None = None,
) -> None:
    """Hard gate for todo-sourced implement dispatch. No-op for non-todo sources."""
    if source_ref is None:
        return

    ref = parse_source_ref(source_ref)
    if ref.source_kind != SourceKind.TODO.value:
        return

    entity = cortex.entity_get(ref.canonical_ref, intent="full")
    attrs = decode_gate_attributes(entity.get("attributes"))
    triage = (attrs.get("density_triage") or "").strip()
    now_iso = datetime.now(UTC).isoformat()

    aid = coerce_assertion_id(attrs.get("implement_ready_assertion_id"))
    assertion: dict[str, Any] | None = None
    if aid is not None:
        loaded = cortex.assertion_get(aid)
        if isinstance(loaded, dict) and "error" not in loaded:
            assertion = loaded

    if triage == "judgment_required" and pin_needs_resolution(
        assertion, todo_id=ref.canonical_ref, now_iso=now_iso
    ):
        resolved = resolve_fresh_implement_ready(
            todo_id=ref.canonical_ref, cortex=cortex, now_iso=now_iso
        )
        if resolved is not None:
            aid, assertion = resolved

    evidence = assertion.get("evidence_uris") if assertion else None
    cited_uri: str | None = None
    dense_spec_text: str | None = None
    if isinstance(evidence, list):
        cited_uri = select_cited_dense_spec_uri(
            evidence, source_uri=entity.get("source_uri")
        )
        if cited_uri is not None:
            dense_spec_text = read_dense_spec_text(
                cited_uri, workspaces_root=workspaces_root
            )

    raw_files = attrs.get("files_expected")
    files_expected = raw_files if isinstance(raw_files, list) else []
    raw_acs = attrs.get("acceptance_criteria")
    acceptance_criteria = raw_acs if isinstance(raw_acs, list) else []

    spec_hash_uri = dense_spec_hash_uri(dense_spec_text) if dense_spec_text else None
    if triage == "judgment_required":
        skeptic_outcome = resolve_skeptic_ratification(
            todo_id=ref.canonical_ref,
            cortex=cortex,
            now_iso=now_iso,
            spec_hash_uri=spec_hash_uri,
            resolve_skeptic=resolve_skeptic,
        )
    else:
        skeptic_outcome = SkepticRatificationOutcome(ratified=False)

    raw_waived = attrs.get("recon_waived")
    recon_waived = recon_waived_bool(raw_waived)
    recon_waiver = parse_recon_waiver(raw_waived)

    verdict = evaluate_implement_ready(
        todo_id=ref.canonical_ref,
        density_triage=attrs.get("density_triage"),
        source_uri=entity.get("source_uri"),
        implement_ready_assertion_id=aid,
        assertion=assertion,
        now_iso=now_iso,
        dense_spec_uri=cited_uri,
        dense_spec_text=dense_spec_text,
        files_expected=files_expected,
        acceptance_criteria=acceptance_criteria,
        entity_name=entity.get("name"),
        skeptic_ratified=skeptic_outcome.ratified,
        recon_waived=recon_waived,
        skeptic_evidence_grounded=skeptic_outcome.evidence_grounded,
        skeptic_evidence_unresolved=skeptic_outcome.evidence_unresolved,
        skeptic_evidence_mode=skeptic_outcome.evidence_mode,
        skeptic_unratified_reason=skeptic_outcome.reason,
    )
    if not verdict.admitted:
        raise ImplementReadyGateError(
            request_id=request_id,
            field="source_ref",
            reason=verdict.reason or verdict.code or "implement_not_ready",
            code=verdict.code or "implement_not_ready",
        )

    if triage == "judgment_required" and dense_spec_text is not None:
        attestation_verdict = evaluate_doc_validate_attestation(
            doc_type=_DEFAULT_DOC_TYPE,
            spec_text=dense_spec_text,
            evidence_uris=evidence if isinstance(evidence, list) else None,
            preflight_kwargs={
                "todo_id": ref.canonical_ref,
                "density_triage": attrs.get("density_triage"),
                "source_uri": entity.get("source_uri"),
                "implement_ready_assertion_id": aid,
                "assertion": assertion,
                "now_iso": now_iso,
                "dense_spec_uri": cited_uri,
                "dense_spec_text": dense_spec_text,
                "files_expected": files_expected,
                "acceptance_criteria": acceptance_criteria,
                "entity_name": entity.get("name"),
                "skeptic_ratified": skeptic_outcome.ratified,
                "recon_waived": recon_waived,
                "recon_waiver": recon_waiver.to_gate_sibling() if recon_waiver else None,
            },
            skip_side_effect_guard=skip_doc_validate_guard,
        )
        if not attestation_verdict.admitted:
            raise ImplementReadyGateError(
                request_id=request_id,
                field="source_ref",
                reason=attestation_verdict.reason
                or attestation_verdict.code
                or "doc_validate_attestation_required",
                code=attestation_verdict.code or "doc_validate_attestation_required",
            )


__all__ = [
    "DocValidateAttestation",
    "DocValidateAttestationVerdict",
    "ImplementReadyCortex",
    "ImplementReadyGateError",
    "doc_validate_attestation_tokens",
    "evaluate_doc_validate_attestation",
    "extract_doc_validate_attestation",
    "require_implement_ready",
]
