"""Review-attestation findings and warnings — extracted from drift_gates for SLOC compliance.

check_review_attestation (Gate RA evaluation) stays in drift_gates because it calls
evaluate_drift_gate/gate_state from that module; circular imports are avoided by keeping
the gate-evaluation caller co-located with the drift-gate machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from implement_admission.routing import classify_risk_tier, risk_tier_rank
from implement_admission.spec import (
    ImplementSpec,
    ReadinessState,
    implement_spec_hash,
)


class ReviewAttestationCode(StrEnum):
    MISSING_ATTESTATION = "MISSING_ATTESTATION"
    RISK_UNDERCLASSIFIED = "RISK_UNDERCLASSIFIED"
    NO_PASSING_REVIEW = "NO_PASSING_REVIEW"
    UNBOUND_REVIEW = "UNBOUND_REVIEW"
    STALE_REVIEW = "STALE_REVIEW"
    UNRESOLVED_BLOCKERS = "UNRESOLVED_BLOCKERS"


@dataclass(frozen=True, slots=True)
class ReviewAttestationFinding:
    code: ReviewAttestationCode
    message: str
    rejectable_under_enforce: bool


def review_attestation_findings(spec: ImplementSpec) -> list[ReviewAttestationFinding]:
    """Typed review-attestation findings — recompute floor from spec; never raises."""
    if spec.readiness.state != ReadinessState.READY:
        return []

    att = spec.provenance.review_attestation
    req_tier = classify_risk_tier(spec)
    fam = att.author_family if att else "claude"
    floor_required = req_tier in {"material", "critical"} and fam == "claude"
    if not floor_required:
        return []

    findings: list[ReviewAttestationFinding] = []
    if att is None:
        findings.append(
            ReviewAttestationFinding(
                code=ReviewAttestationCode.MISSING_ATTESTATION,
                message=(
                    f"review required (tier={req_tier}) but no review_attestation present."
                ),
                rejectable_under_enforce=True,
            )
        )
        return findings

    if not att.required or risk_tier_rank(att.risk_tier) < risk_tier_rank(req_tier):
        findings.append(
            ReviewAttestationFinding(
                code=ReviewAttestationCode.RISK_UNDERCLASSIFIED,
                message=(
                    "review_attestation under-classifies risk "
                    f"(stored={att.risk_tier}, recomputed={req_tier})."
                ),
                rejectable_under_enforce=False,
            )
        )

    if att.disposition in {"missing", "pending", "blocked"}:
        findings.append(
            ReviewAttestationFinding(
                code=ReviewAttestationCode.NO_PASSING_REVIEW,
                message=(
                    f"no passing cross-family review (disposition={att.disposition})."
                ),
                rejectable_under_enforce=True,
            )
        )

    if att.disposition in {"pass", "pass_with_conditions"} and att.spec_hash is None:
        findings.append(
            ReviewAttestationFinding(
                code=ReviewAttestationCode.UNBOUND_REVIEW,
                message="UNBOUND pass — review not bound to any spec_hash.",
                rejectable_under_enforce=True,
            )
        )

    current = implement_spec_hash(spec)
    if att.spec_hash is not None and att.spec_hash != current:
        findings.append(
            ReviewAttestationFinding(
                code=ReviewAttestationCode.STALE_REVIEW,
                message=f"STALE — review bound to {att.spec_hash}, packet now {current}.",
                rejectable_under_enforce=True,
            )
        )

    if att.unresolved_blocker_ids:
        n = len(att.unresolved_blocker_ids)
        ids = ", ".join(att.unresolved_blocker_ids)
        findings.append(
            ReviewAttestationFinding(
                code=ReviewAttestationCode.UNRESOLVED_BLOCKERS,
                message=f"{n} unresolved blocker(s): {ids}.",
                rejectable_under_enforce=False,
            )
        )

    return findings


def review_attestation_warnings(spec: ImplementSpec) -> list[str]:
    """Backward-compat string projection over typed findings."""
    return [f.message for f in review_attestation_findings(spec)]
