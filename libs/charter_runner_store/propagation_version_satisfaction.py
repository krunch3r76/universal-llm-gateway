"""Three-case version-satisfaction machine for propagation ledger terminalization.

Predicate cause (git relation between row ``code_ref`` and probed ``code_version``)
is separate from gate conditions (outgoing generation, process identity). This
module binds only the relation→case mapping; callers apply gates per case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from deploy_identity.code_ref_relation import (
    CodeRefRelation,
    code_ref_relation_from_observed,
)

VersionSatisfactionCase = Literal[
    "exact_match",
    "ancestry_satisfied",
    "unrelated_or_unresolvable",
    "stale_code",
]

# Defer tokens persisted on open rows — not terminal statuses.
DEFER_ANCESTRY_SATISFIED = "version_superseded_by_newer_code"
DEFER_UNRELATED_OR_UNRESOLVABLE = "version_relation_unrelated_or_unresolvable"

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
    "unrelated_or_unresolvable": (
        "No git ancestry links the row code_ref to the observed version, "
        "or the ref could not be resolved — not a merits failure."
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
    """Map ``(code_ref, observed)`` to one of the three terminal-policy cases.

    ``stale_code`` (observed is an ancestor of ``code_ref``) is the merits-failure
    path and is distinct from case (iii).
    """
    relation = code_ref_relation_from_observed(code_ref, observed)
    if relation == "equal":
        case: VersionSatisfactionCase = "exact_match"
    elif relation == "ancestor":
        case = "ancestry_satisfied"
    elif relation == "descendant-of-observed":
        case = "stale_code"
    else:
        case = "unrelated_or_unresolvable"
    return VersionSatisfaction(
        case=case,
        relation=relation,
        reader_entitlement=_CASE_READER_ENTITLEMENT[case],
    )


__all__ = [
    "DEFER_ANCESTRY_SATISFIED",
    "DEFER_UNRELATED_OR_UNRESOLVABLE",
    "VersionSatisfaction",
    "VersionSatisfactionCase",
    "classify_version_satisfaction",
]
