"""Version-satisfaction cases for propagation ledger terminalization.

Predicate cause (git relation between row ``code_ref`` and probed ``code_version``)
is separate from gate conditions (outgoing generation, process identity). This
module binds only the relation→case mapping; callers apply gates per case.

Defect B (arc 6885): ``unresolvable`` (not a git object) is distinct from
``unrelated`` (valid commit, no ancestry to observed). Collapsing them trained
readers to ignore ``open``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from deploy_identity import code_ref_relation as code_ref_relation_mod
from deploy_identity.code_ref_relation import (
    CodeRefRelation,
    code_ref_relation_from_observed,
)

VersionSatisfactionCase = Literal[
    "exact_match",
    "ancestry_satisfied",
    "unresolvable",
    "unrelated",
    "stale_code",
]

# Defer tokens persisted on open rows — not terminal statuses.
DEFER_ANCESTRY_SATISFIED = "version_superseded_by_newer_code"
# Legacy token (pre-split); retained for reading older open-row defer_reason values.
DEFER_UNRELATED_OR_UNRESOLVABLE = "version_relation_unrelated_or_unresolvable"
DEFER_UNRELATED = "version_relation_unrelated"

_CASE_READER_ENTITLEMENT: dict[VersionSatisfactionCase, str] = {
    "exact_match": (
        "This row's deploy is proven: probed code_version equals the row code_ref."
    ),
    "ancestry_satisfied": (
        "This row's own deploy was never identity-proven; newer code is "
        "running, so the ancestor obligation is transitively satisfied and "
        "the open row retires without a merits failure. "
        "READ-CAVEAT: closed ancestry rows are events, not a standing "
        "liveness oracle — ask observe_code_ref_live (fresh process probe) "
        "for is-code_ref-live."
    ),
    "unresolvable": (
        "The row code_ref is not a git commit object — proof can never be met "
        "by ordinary restart. This is an attempt defect (STATUS_CLAIM_KIND="
        "observed_of_attempt), not pending-proof debt."
    ),
    "unrelated": (
        "The row code_ref resolves but shares no git ancestry with the observed "
        "version — typically an undeployed branch tip. Ordinary deploy-line "
        "restarts will not satisfy it."
    ),
    "stale_code": (
        "Observed code is older than the row target — a merits mismatch "
        "when attribution to the incoming generation is established."
    ),
}


@dataclass(frozen=True)
class VersionSatisfaction:
    """Classified relation between one row target and one observed version."""

    case: VersionSatisfactionCase
    relation: CodeRefRelation
    reader_entitlement: str


def classify_version_satisfaction(
    code_ref: str,
    observed: str | None,
) -> VersionSatisfaction:
    """Map ``(code_ref, observed)`` to a terminal-policy case.

    ``unresolvable`` (expected ref is not a commit object) is distinct from
    ``unrelated`` (valid ref, no ancestry). ``stale_code`` remains the
    merits-failure path when observed is an ancestor of ``code_ref``.
    """
    relation = code_ref_relation_from_observed(code_ref, observed)
    if code_ref_relation_mod.resolve_commit_sha(str(code_ref or "").strip()) is None:
        case: VersionSatisfactionCase = "unresolvable"
    elif relation == "equal":
        case = "exact_match"
    elif relation == "ancestor":
        case = "ancestry_satisfied"
    elif relation == "descendant-of-observed":
        case = "stale_code"
    else:
        # Includes relation == "unknown" (no observed) and true unrelated.
        case = "unrelated"
    return VersionSatisfaction(
        case=case,
        relation=relation,
        reader_entitlement=_CASE_READER_ENTITLEMENT[case],
    )


__all__ = [
    "DEFER_ANCESTRY_SATISFIED",
    "DEFER_UNRELATED",
    "DEFER_UNRELATED_OR_UNRESOLVABLE",
    "VersionSatisfaction",
    "VersionSatisfactionCase",
    "classify_version_satisfaction",
]
