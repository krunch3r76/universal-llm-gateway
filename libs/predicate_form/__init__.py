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

from .classes import (
    apply_class_1,
    apply_class_2,
    apply_class_3,
    apply_class_4,
    class_6_check,
)
from .entity_resolve import (
    CortexEntityResolver,
    EntityResolver,
    StaticEntityResolver,
)
from .parser import Predicate, PredicateParseError, parse, unparse


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


def normalize_predicate_domain(
    entity_id: str,
    predicate_form: str,
    claim_text: str | None = None,
    *,
    resolver: EntityResolver | None = None,
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
    p, fired = apply_class_2(p, active_resolver)
    if fired:
        classes_applied.append(2)

    requires_review = class_6_check(entity_id, p)
    if requires_review:
        classes_applied.append(6)

    canonical_form = unparse(p)
    domain_key = unparse(_strip_prefixes(p))
    return {
        "domain_key": domain_key,
        "canonical_form": canonical_form,
        "classes_applied": classes_applied,
        "requires_human_review": requires_review,
    }


__all__ = [
    "CortexEntityResolver",
    "EntityResolver",
    "Predicate",
    "PredicateParseError",
    "StaticEntityResolver",
    "normalize_predicate_domain",
    "parse",
    "unparse",
]
