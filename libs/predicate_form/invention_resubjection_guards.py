"""Write-time invention (Check 1) and re-subjection (Check 2) guards.

Peer to ``decision_self_status`` — NOT clustering classes 1–6. Both checks
set ``requires_human_review`` on the normalize path when they fire; they
never hard-reject (422).

Check 1 — invention: any bare predicate argument absent from claim text
(hyphen↔underscore tolerant) and absent from the preferred vocabulary
(``predicate_extract/v1/prompts.yaml`` canonical predicates + ``current`` /
``former``) is treated as model-invented.

Check 2 — re-subjection: the parsed first argument must equal the assertion
bearer's ``entity_id`` (string equality after normalization classes).
"""

from __future__ import annotations

from .parser import Predicate

# Canonical predicate names from pipelines/predicate_extract/v1/prompts.yaml
# system_prompt ("Prefer canonical predicates such as:"). Append-only here
# mirrors the prompt list; extend when the prompt vocabulary grows.
_PREFERRED_PREDICATE_NAMES: frozenset[str] = frozenset(
    {
        "is_a",
        "role",
        "title",
        "has_attribute",
        "has_quality",
        "located_in",
        "based_in",
        "member_of",
        "affiliated_with",
        "employed_by",
        "part_of",
        "has",
        "status",
        "describes",
        "color",
        "size",
    }
)

_PREFERRED_MODIFIERS: frozenset[str] = frozenset({"current", "former"})

PREFERRED_VOCABULARY: frozenset[str] = _PREFERRED_PREDICATE_NAMES | _PREFERRED_MODIFIERS


def _token_variants(token: str) -> set[str]:
    """Lowercase hyphen/underscore variants for substring search."""
    lower = token.lower()
    return {lower, lower.replace("_", "-"), lower.replace("-", "_")}


def _token_in_claim(token: str, claim_text: str) -> bool:
    claim_lower = claim_text.lower()
    return any(v in claim_lower for v in _token_variants(token))


def _is_entity_reference(arg: str) -> bool:
    """Prefixed entity ids and numeric literals are not invention targets."""
    if ":" in arg:
        return True
    try:
        float(arg)
        return True
    except ValueError:
        return False


def check_invention(claim_text: str | None, p: Predicate) -> bool:
    """Return True when any bare arg looks invented relative to claim + vocab."""
    if not claim_text or not claim_text.strip():
        return False
    for arg in p.args:
        if _is_entity_reference(arg):
            continue
        if arg.lower() in PREFERRED_VOCABULARY:
            continue
        if _token_in_claim(arg, claim_text):
            continue
        return True
    return False


def check_resubjection(entity_id: str, p: Predicate) -> bool:
    """Return True when the first arg is a prefixed entity id != bearer."""
    if not p.args:
        return False
    first = p.args[0]
    if ":" not in first:
        return False
    return first != entity_id
