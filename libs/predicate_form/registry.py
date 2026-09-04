"""Append-only registries for Classes 1, 4, 6.

Class 1 — state-token synonyms (alias → canonical).
Class 4 — predicate-shape variants (legacy 2-arg ↔ 3-arg canonical).
Class 6 — generic-state token set guarding accidental cross-entity merges.

Append-only invariant (per v4 §4.1): existing rows are never edited or
removed; new rows extend the table. This preserves clustering stability
across substrate revisions — a token's canonical form once chosen is
stable forever.
"""

from __future__ import annotations

# Class 1 — state-token synonyms.
#
# Empirical seed kept small. The §4.1 example
# (`reassigned_to_another_department` → `reassigned`) is the only
# ratified entry so far. The Class 1 table grows append-only as the
# §14.2 backfill / write-path validator surfaces additional aliases.
CLASS_1_STATE_SYNONYMS: dict[str, str] = {
    "reassigned_to_another_department": "reassigned",
}

# Class 4 — shape-variant table.
#
# Each entry maps a 2-arg "squashed" predicate-name + arg pattern to its
# canonical 3-arg form. Empirical seed from v1.3 wave-1 backfill (ledger
# C22 cluster, assertion 4390): `has_attribute(<entity>,
# filing_fees_total_<int>_<frac>)` ↔ `has_attribute(<entity>, filing_fees,
# <int>.<frac>)`.
#
# Schema:
#   key   = (predicate_name, squashed_token_prefix)
#   value = canonical (predicate_name, attr_name, value_template)
#
# Detection at runtime is structural rather than table-driven for the
# numeric-suffix family — a single rule covers the entire
# `filing_fees_total_<N>_<F>` style. Future Class 4 additions go here as
# explicit shape-pair rules.
CLASS_4_SHAPE_RULES: tuple[dict, ...] = (
    {
        "name": "filing_fees_total_split",
        "predicate": "has_attribute",
        "match_arg_prefix": "filing_fees_total_",
        # Trailing pattern after prefix: `<int>_<frac>` → `<int>.<frac>`
        "canonical_attr": "filing_fees",
    },
)

# Class 6 — generic-state guard set.
#
# Predicates with a state token in this set against a non-workflow-state
# entity are flagged for human review rather than silently merged.
# Workflow-state-tracked entity types (`todo:*`, `plan_phase:*`) are
# exempt — those entities' entire purpose is to carry these tokens.
CLASS_6_GENERIC_STATES: frozenset[str] = frozenset(
    {
        "pending",
        "accepted",
        "rejected",
        "current",
        "active",
        "completed",
        "in_progress",
        "deferred",
        "cancelled",
    }
)

CLASS_6_WORKFLOW_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "todo",
        "plan_phase",
    }
)

# Status-functor family shared by class_6 slot selection and card pin WHERE.
# Wave 1 graduation honesty (todo:cortex-predicate-normalize-graduation-honesty):
# one constant consumed by classes.py + card.py — not a fifth grammar mirror.
STATUS_FUNCTOR_FAMILY: frozenset[str] = frozenset({"status"})


def is_status_functor(predicate_name: str) -> bool:
    """True for ``status`` and ``*_status`` functors (arity-3 state slot)."""
    return predicate_name in STATUS_FUNCTOR_FAMILY or predicate_name.endswith("_status")


def status_functor_state_token(args: tuple[str, ...], predicate_name: str) -> str | None:
    """Return the state-slot token for status-functor predicates."""
    if not args:
        return None
    if is_status_functor(predicate_name) and len(args) >= 3:
        return args[1]
    return args[-1]


def status_current_predicate_sql_where() -> str:
    """SQL fragment matching status(%, current) and %_status(%, current) rows."""
    return (
        "predicate_form IS NOT NULL AND ("
        "LOWER(predicate_form) LIKE 'status(%, current)' "
        "OR LOWER(predicate_form) LIKE '%\\_status(%, current)' ESCAPE '\\'"
        ")"
    )
