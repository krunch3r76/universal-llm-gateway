"""`normalize_predicate_domain()` — substrate-spec v1.3 canonicalizer.

Maps a stored ``predicate_form`` plus its assertion's ``entity_id`` to a
two-form canonical:

    {
        "domain_key":     <bare form>,        # §4.1 clustering equivalence
        "canonical_form": <prefixed form>,    # §3.2 storage convention
        "classes_applied": [<int>, ...],
        "requires_human_review": <bool>,      # Class 6 guard
    }

Spec cross-references:
- v4 §4.1  — Class 1-6 definitions and pseudocode
- v4 §3.2  — bit-for-bit canonical convention (function-output spec)
- v4 §10.4 — Path B programmatic re-run (acceptance gate)
- v4 §10.6 — CM-Select-with-patch verification (8 Q1 fixtures)

Q1 (cursor dispatch packet) is ratified as option (c): Class 2 only
rewrites slugs that have an EXACT match in ``entities.id`` —
non-matching bare tokens stay bare. Q2 is ratified as option (b): both
``domain_key`` (bare) and ``canonical_form`` (prefixed) are returned in
the same call.

Class 1 synonym registry is APPEND-ONLY — once an alias maps to a
canonical, the mapping is permanent. This is the substrate's only
across-revision stability guarantee for state-token clustering.
"""

from __future__ import annotations

import hashlib

from .action_enrichment import (
    EnrichmentPreview,
    dry_run_enrich_assertions,
    enrich_action_predicate_from_claim,
)
from .action_vocabulary import (
    ACTION_VOCAB_V0,
    ActionPredicate,
    parse_action_predicate,
    party_from_entity_id,
)
from .classes import (
    apply_class_1,
    apply_class_2,
    apply_class_3,
    apply_class_4,
    class_6_check,
    correct_decision_self_status,
    decision_status_token,
    is_decision_self_status,
)
from .collision import (
    CollisionResult,
    Contradiction,
    SupersededByCandidate,
    detect_contradictions,
)
from .entity_resolve import (
    CortexEntityResolver,
    DBEntityResolver,
    EntityResolver,
    ResolutionResult,
    StaticEntityResolver,
    bare_token_to_slug,
)
from .parser import Predicate, PredicateParseError, parse, unparse

NORMALIZER_VERSION = "v1.3.1"


def _strip_prefixes(p: Predicate) -> Predicate:
    """Strip `<type>:` prefixes from prefixed args; preserve bare args.

    Used to derive the bare-form ``domain_key`` from the canonical
    prefixed form. Hyphens within slugs are converted to underscores so
    the bare key matches the legacy stored form's separator convention
    (§3.2 amendment 2 — non-entity terminals use underscores).
    """
    new_args = []
    for arg in p.args:
        if ":" in arg:
            _, slug = arg.split(":", 1)
            new_args.append(slug.replace("-", "_"))
        else:
            new_args.append(arg)
    return Predicate(p.name, tuple(new_args))


def _is_numeric(s: str) -> bool:
    """Local copy of classes._is_numeric for ledger eligibility checks."""
    try:
        float(s)
        return True
    except ValueError:
        return False


def normalize_predicate_domain(
    entity_id: str,
    predicate_form: str,
    claim_text: str | None = None,
    *,
    resolver: EntityResolver | None = None,
    entity_workflow_state: str | None = None,
) -> dict:
    """Canonicalize a predicate_form to its (domain_key, canonical_form) pair.

    Args:
        entity_id: The assertion's bearer entity_id (e.g. ``person:foo``).
            Used by Class 6 to decide whether a generic-state predicate
            requires human review.
        predicate_form: The stored predicate_form string.
        claim_text: Reserved for Class 5 (NULL-predicate_form fallback);
            unused in the current return path. Accepting it keeps the
            signature stable for the §14.1 backfill dispatch.
        resolver: Optional EntityResolver. Defaults to a cortex-backed
            resolver; tests and offline callers inject StaticEntityResolver.
        entity_workflow_state: Optional tracked workflow_state of the bearer
            entity (write path only). When supplied for a ``decision:`` bearer,
            enables the self-status polarity guard: a self-referential
            ``status(self, X)`` predicate whose token contradicts the tracked
            state is rewritten to match it, and a faithful self-status is
            exempted from the Class 6 review flag. Omitted (None) on the
            clustering / backfill path → guard is a strict no-op.

    Returns:
        dict with keys ``domain_key`` (bare form), ``canonical_form``
        (prefixed form), ``classes_applied`` (list of class numbers
        that fired), and ``requires_human_review`` (bool from Class 6).

    Raises:
        PredicateParseError: predicate_form is not parseable.
    """
    p = parse(predicate_form)
    classes_applied: list[int] = []

    p, fired = apply_class_1(p)
    if fired:
        classes_applied.append(1)

    p, fired = apply_class_4(p)
    if fired:
        classes_applied.append(4)

    p, fired = apply_class_3(p)
    if fired:
        classes_applied.append(3)

    active_resolver: EntityResolver = (
        resolver if resolver is not None else CortexEntityResolver()
    )

    # v1.3.1 ledger computation (shadow cardinality, does not affect canonical_form).
    # Run over args that Class 2 sees (post 1/3/4). Aggregate per §4 of work-order.
    per_arg_decisions: list[str] = []
    per_arg_fps: list[str] = []
    has_eligible = False
    for arg in p.args:
        if ":" in arg:
            per_arg_fps.append("")
            per_arg_decisions.append("")
            continue
        if _is_numeric(arg):
            per_arg_fps.append("")
            per_arg_decisions.append("")
            continue
        has_eligible = True
        slug = bare_token_to_slug(arg)
        res = active_resolver.resolve_slug_with_cardinality(slug)
        per_arg_decisions.append(res.decision)
        per_arg_fps.append(res.candidate_fingerprint)

    if any(d == "collision_refused" for d in per_arg_decisions):
        normalization_decision = "collision_refused"
    elif any(d == "no_match" for d in per_arg_decisions):
        normalization_decision = "no_match"
    else:
        normalization_decision = "resolved_single"
    if not has_eligible:
        normalization_decision = "resolved_single"
        candidate_set_fingerprint = ""
    else:
        joined = "|".join(per_arg_fps)
        candidate_set_fingerprint = hashlib.sha256(joined.encode("utf-8")).hexdigest()[
            :16
        ]

    p, fired = apply_class_2(p, active_resolver)
    if fired:
        classes_applied.append(2)

    # Write-time decision self-status polarity guard (NOT a clustering class).
    # Fires only when the bearer's workflow_state is supplied (write path); the
    # clustering / §14.1 backfill path passes none, so this is a strict no-op
    # there and existing canonical fixed points are preserved. See classes.py
    # header + agent-bus thread 1267.
    p, decision_self_status_corrected = correct_decision_self_status(
        entity_id, p, entity_workflow_state
    )

    requires_review = class_6_check(entity_id, p)
    # A *faithful* decision self-status (state token == the entity's tracked
    # workflow_state) is a correct projection of a tracked state, not an
    # accidental cross-entity merge — so it is exempt from the Class 6
    # human-review flag. Only applies when workflow_state context confirms
    # faithfulness; without that context we stay conservative and let Class 6
    # flag as before.
    _desired_self_status = decision_status_token(entity_workflow_state)
    decision_self_status_faithful = (
        is_decision_self_status(entity_id, p)
        and _desired_self_status is not None
        and p.args[1] == _desired_self_status
    )
    if requires_review and decision_self_status_faithful:
        requires_review = False
    if requires_review:
        classes_applied.append(6)

    canonical_form = unparse(p)
    domain_key = unparse(_strip_prefixes(p))
    return {
        "domain_key": domain_key,
        "canonical_form": canonical_form,
        "classes_applied": classes_applied,
        "requires_human_review": requires_review,
        # v1.3.1 ledger fields
        "raw_predicate_form": predicate_form,
        "normalization_decision": normalization_decision,
        "candidate_set_fingerprint": candidate_set_fingerprint,
        "normalizer_version": NORMALIZER_VERSION,
        "decision_self_status_corrected": decision_self_status_corrected,
    }


__all__ = [
    "ACTION_VOCAB_V0",
    "ActionPredicate",
    "CollisionResult",
    "Contradiction",
    "CortexEntityResolver",
    "DBEntityResolver",
    "EnrichmentPreview",
    "EntityResolver",
    "Predicate",
    "PredicateParseError",
    "ResolutionResult",
    "StaticEntityResolver",
    "SupersededByCandidate",
    "detect_contradictions",
    "dry_run_enrich_assertions",
    "enrich_action_predicate_from_claim",
    "normalize_predicate_domain",
    "parse",
    "parse_action_predicate",
    "party_from_entity_id",
    "unparse",
]
