"""Classes 1-6 — predicate-form normalization passes.

Each `apply_class_N` function takes a Predicate (and optional context)
and returns a tuple ``(new_predicate, fired)`` where ``fired`` is True
iff the class actually rewrote anything. The orchestrator in
``__init__`` chains them in fixed order (1 → 4 → 3 → 2 → 6, with Class
5 reserved for the NULL-predicate_form path) and accumulates the list
of classes that fired.

Order rationale:
- Class 1 (state synonyms) and Class 4 (shape variants) operate on
  raw legacy tokens before Class 2 lifts them to entity URIs.
- Class 3 (case-fold) runs before Class 2 because the case-folded slug
  is what we'd compare against `entities.id` (which is lowercase by
  convention).
- Class 2 (entity-prefix) runs last among rewriting classes because it
  produces the prefixed canonical form used in §3.2 storage.
- Class 6 (generic-state guard) is read-only; it inspects the final
  predicate against the assertion's `entity_id` and flags rather than
  rewriting.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from .entity_resolve import EntityResolver, bare_token_to_slug
from .parser import Predicate
from .registry import (
    CLASS_1_STATE_SYNONYMS,
    CLASS_4_SHAPE_RULES,
    CLASS_6_GENERIC_STATES,
    CLASS_6_WORKFLOW_ENTITY_TYPES,
)


class ClassResult(NamedTuple):
    predicate: Predicate
    fired: bool


# Class 3 — month-name regex. Matches `<MonthName>_<day>_<year>` shapes.
_MONTH_NAMES = (
    "January|February|March|April|May|June|July|August|September|"
    "October|November|December"
)
_MONTH_DATE_RE = re.compile(rf"^({_MONTH_NAMES})_(\d{{1,2}})_(\d{{4}})$")

# Class 4 — `filing_fees_total_<int>_<frac>` decoder.
_FEES_TOTAL_RE = re.compile(r"^filing_fees_total_(\d+)_(\d+)$")


def apply_class_1(p: Predicate) -> ClassResult:
    """Class 1 — state-token synonym canonicalization.

    Rewrites any arg whose value is a key in CLASS_1_STATE_SYNONYMS to
    its canonical form. Append-only registry; existing aliases never
    relocate.
    """
    new_args = tuple(CLASS_1_STATE_SYNONYMS.get(a, a) for a in p.args)
    return ClassResult(Predicate(p.name, new_args), new_args != p.args)


def apply_class_3(p: Predicate) -> ClassResult:
    """Class 3 — argument-form canonicalization (case-fold).

    Lowercases case-IDs (e.g. `24PR197054` → `24pr197054`, also strips
    leading `case_`) and month-name dates (`August_21_2024` →
    `august_21_2024` per amendment 1, ratified today). Other args are
    left untouched — Class 3 specifically targets identifier-shaped
    string tokens, not arbitrary prose.
    """
    rewrote = False
    new_args = []
    for arg in p.args:
        rewritten = arg
        # Case-ID: optional leading `case_`, alphanumeric body.
        had_prefix = rewritten.startswith("case_")
        body = rewritten[5:] if had_prefix else rewritten
        if _looks_like_case_id(body):
            rewritten = body.lower()
        # Month-name date — runs on the (possibly case-stripped) form.
        m = _MONTH_DATE_RE.match(rewritten)
        if m:
            rewritten = f"{m.group(1).lower()}_{m.group(2)}_{m.group(3)}"
        if rewritten != arg:
            rewrote = True
        new_args.append(rewritten)
    return ClassResult(Predicate(p.name, tuple(new_args)), rewrote)


def _looks_like_case_id(s: str) -> bool:
    """Heuristic: alphanumeric, mixed digits and ≥2 letters, length 6-20.

    Distinguishes case-IDs (`24PR197054`) from other tokens. Numeric-only
    tokens (`450`, `500000`) are not case-IDs; pure words (`filer`) are
    not case-IDs.
    """
    if not (6 <= len(s) <= 20):
        return False
    if not s.isalnum():
        return False
    has_digit = any(c.isdigit() for c in s)
    letter_count = sum(1 for c in s if c.isalpha())
    return has_digit and letter_count >= 2


def apply_class_4(p: Predicate) -> ClassResult:
    """Class 4 — predicate-shape variant canonicalization.

    Currently covers the `filing_fees_total_<int>_<frac>` 2-arg squashed
    form. Detection is structural (regex on arg2) rather than
    table-driven; the registry's CLASS_4_SHAPE_RULES enumerates the
    families that have a structural rule.
    """
    if p.name != "has_attribute" or len(p.args) != 2:
        return ClassResult(p, False)
    for rule in CLASS_4_SHAPE_RULES:
        if rule["name"] == "filing_fees_total_split":
            m = _FEES_TOTAL_RE.match(p.args[1])
            if m:
                whole, frac = m.group(1), m.group(2)
                value = f"{whole}.{frac}"
                return ClassResult(
                    Predicate(p.name, (p.args[0], rule["canonical_attr"], value)),
                    True,
                )
    return ClassResult(p, False)


def apply_class_2(p: Predicate, resolver: EntityResolver) -> ClassResult:
    """Class 2 — entity-prefix canonicalization (bare → prefixed).

    For each argument, if the token's slug form has an exact match
    against ``entities.id``, rewrite to the prefixed canonical
    (`person:camelia-mahmoudi`). No fuzzy matching — Q1 ratified
    option (c). Unmatched bare tokens stay bare.

    This is the heavy class for the 8 §10.6 Q1 fixtures. It runs after
    Class 3 so case-folded tokens are checked.
    """
    rewrote = False
    new_args = []
    for arg in p.args:
        # Already-prefixed args (contain `:`) pass through.
        if ":" in arg:
            new_args.append(arg)
            continue
        # Numeric-only or numeric-with-decimal args pass through.
        if _is_numeric(arg):
            new_args.append(arg)
            continue
        slug = bare_token_to_slug(arg)
        match = resolver.resolve_slug(slug)
        if match is not None:
            new_args.append(match)
            rewrote = True
        else:
            new_args.append(arg)
    return ClassResult(Predicate(p.name, tuple(new_args)), rewrote)


def _is_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def class_6_check(entity_id: str, p: Predicate) -> bool:
    """Class 6 — generic-state guard.

    Returns True if the predicate's last arg is a generic state token
    AND the assertion's entity_id is NOT a workflow-state-tracked type.
    Callers route True returns to `requires_human_review` rather than
    auto-clustering. Read-only — does not rewrite the predicate.
    """
    if not p.args:
        return False
    state_token = p.args[-1]
    if state_token not in CLASS_6_GENERIC_STATES:
        return False
    entity_type = entity_id.split(":", 1)[0] if ":" in entity_id else entity_id
    return entity_type not in CLASS_6_WORKFLOW_ENTITY_TYPES
