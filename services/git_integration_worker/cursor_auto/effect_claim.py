"""Annotate-only effect-claim injection for nested cursor-sdk prompts (arc 7119).

Limb A: lexical effect-claim scan + executor verdict block.
Limb C: quantified set claims oblige per-member enumeration tables.
Annotate-only — never blocks, refuses, or fails a turn.
"""

from __future__ import annotations

import re

from agent_bus_store.disposition import first_line_is_disposition_type

# Seed lexicon from diagnosis §4.3 (arc-7119-prevent-versus-clear).
_EFFECT_PREDICATE_PHRASES: tuple[str, ...] = (
    "stops existing",
    "clears",
    "unblocks",
    "frees",
    "no longer",
    "needs no",
    "not blocked on",
    "dissolves",
    "at once",
    "identically",
    "one change wide",
    "restores",
)

_QUANTIFIER_TOKENS: tuple[str, ...] = (
    "every",
    "all",
    "at once",
    "identically",
    "the whole",
)

_REPAIR_ID_RE = re.compile(
    r"\bR\d+\b"
    r"|\btodo:[a-z0-9][a-z0-9._-]*"
    r"|\b[a-f0-9]{7,40}\b"
    r"|\b(?:spec|arc)-[a-z0-9][a-z0-9._-]*",
    re.IGNORECASE,
)

_NO_ACTION_REQUESTED_RE = re.compile(r"\bNO ACTION REQUESTED\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_EFFECT_INDEX_CHOICES = ("current_state", "future_transitions", "both")


def _normalize_phrase(phrase: str) -> str:
    return " ".join(phrase.lower().split())


def _phrase_in_text(text: str, phrase: str) -> bool:
    return _normalize_phrase(phrase) in _normalize_phrase(text)


def _sentences(text: str) -> list[str]:
    stripped = (text or "").strip()
    if not stripped:
        return []
    parts = _SENTENCE_SPLIT_RE.split(stripped)
    return [p.strip() for p in parts if p.strip()]


def is_effect_claim_scan_eligible(body: str) -> bool:
    """True when limb A/C may scan *body* at nested admit (DIRECTIVE path only).

    ``NO ACTION REQUESTED`` disposition turns (instance 3) are explicitly out of
    scope — the ungated surface where the defect committed upstream.
    """
    text = body or ""
    if _NO_ACTION_REQUESTED_RE.search(text):
        return False
    upper = text.lstrip().upper()
    if upper.startswith("TYPE:"):
        # Pure disposition legs are not nested-directive bodies.
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if first_line_is_disposition_type(first) and "DIRECTIVE" not in first.upper():
            return False
    return True


def _sentence_has_repair_id(sentence: str) -> bool:
    return _REPAIR_ID_RE.search(sentence) is not None


def extract_limb_a_claims(text: str) -> tuple[str, ...]:
    """Return verbatim sentences matching effect predicate + repair identifier."""
    if not is_effect_claim_scan_eligible(text):
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        if not _sentence_has_repair_id(sentence):
            continue
        if not any(_phrase_in_text(sentence, phrase) for phrase in _EFFECT_PREDICATE_PHRASES):
            continue
        key = sentence.strip()
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return tuple(ordered)


def extract_limb_c_claims(text: str) -> tuple[str, ...]:
    """Return verbatim sentences with quantified effect claims over a set."""
    if not is_effect_claim_scan_eligible(text):
        return ()
    ordered: list[str] = []
    seen: set[str] = set()
    for sentence in _sentences(text):
        low = sentence.lower()
        if not any(token in low for token in _QUANTIFIER_TOKENS):
            continue
        if not any(_phrase_in_text(sentence, phrase) for phrase in _EFFECT_PREDICATE_PHRASES):
            # Quantifier + unblock/clear language still required for limb C.
            if not any(
                tok in low for tok in ("unblock", "clear", "free", "restore", "dissolve")
            ):
                continue
        key = sentence.strip()
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return tuple(ordered)


def effect_claim_injection_lines(body: str) -> list[str]:
    """Prompt lines appended by ``build_sdk_message`` when triggers fire."""
    limb_a = extract_limb_a_claims(body)
    limb_c = extract_limb_c_claims(body)
    if not limb_a and not limb_c:
        return []

    lines = [
        "",
        "## Effect claim verification (annotate-only — arc 7119)",
        (
            "The operator asserted repair-effect language in this directive. "
            "This block is annotate-only: it does not block, refuse, or fail the turn. "
            "A false positive costs one §2 paragraph at most."
        ),
        "",
        "Return an explicit verdict in §2 for each extracted claim below.",
        "",
        "Grammar (forced — no delegation to another seat):",
        "effect_claim:  <one line — the asserted effect>",
        "effect_metric: <measurable, e.g. cdp_lane.free_slots | cdp_lane.admission_count>",
        "effect_basis:  <before> -> <after>",
        (
            "effect_index:  "
            + " | ".join(_EFFECT_INDEX_CHOICES)
            + "  (ternary — you must choose; no deferral)"
        ),
    ]

    if limb_a:
        lines.extend(
            [
                "",
                "### Limb A — effect claim(s) (verbatim)",
                *(f'- "{claim}"' for claim in limb_a),
                "",
                "For each claim: state whether `effect_index` is `current_state`, "
                "`future_transitions`, or `both`, and whether the prose claim holds "
                "given an honest `effect_basis`.",
            ]
        )

    if limb_c:
        lines.extend(
            [
                "",
                "### Limb C — quantified set claim(s) (verbatim)",
                *(f'- "{claim}"' for claim in limb_c),
                "",
                "For each quantified claim: emit a **table** with one row per set member "
                "and an individual verdict column (not a single delta).",
            ]
        )

    return lines


__all__ = [
    "EFFECT_PREDICATE_PHRASES",
    "QUANTIFIER_TOKENS",
    "EFFECT_INDEX_CHOICES",
    "extract_limb_a_claims",
    "extract_limb_c_claims",
    "effect_claim_injection_lines",
    "is_effect_claim_scan_eligible",
]

# Public aliases for tests / lexicon introspection.
EFFECT_PREDICATE_PHRASES = _EFFECT_PREDICATE_PHRASES
QUANTIFIER_TOKENS = _QUANTIFIER_TOKENS
EFFECT_INDEX_CHOICES = _EFFECT_INDEX_CHOICES
