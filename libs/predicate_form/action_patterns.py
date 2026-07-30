"""Per-domain action patterns — sole declaration for vocab + detection (arc 6386 §6b)."""

from __future__ import annotations

import re

_MORTGAGE_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"spread(?:\s+the)?\s+escrow\s+shortage|"
            r"escrow\s+shortage\s+spread|"
            r"spread\s+extension|"
            r"extend\s+escrow\s+shortage\s+spread",
            re.I,
        ),
        "spread_extension",
    ),
    (
        re.compile(
            r"lower[\s-]?payment|payment\s+reduction|reduce\s+payment",
            re.I,
        ),
        "payment_reduction",
    ),
    (re.compile(r"escrow\s+analysis", re.I), "escrow_analysis"),
    (re.compile(r"loan\s+modification", re.I), "loan_modification"),
    (re.compile(r"hardship\s+program", re.I), "hardship_program"),
)

_TAX_APPEAL_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"BOE-305-AH|"
            r"assessment\s+appeal\s+application|"
            r"invalid\s*[–-]\s*closed",
            re.I,
        ),
        "assessment_appeal_application",
    ),
    (
        re.compile(
            r"appeal\s+reinstatement|"
            r"reinstatement\s+request|"
            r"\bRFR\b|"
            r"request\s+for\s+reinstatement",
            re.I,
        ),
        "appeal_reinstatement",
    ),
)

_ACTION_PATTERNS_BY_DOMAIN: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "mortgage_escrow": _MORTGAGE_ACTION_PATTERNS,
    "tax_appeal": _TAX_APPEAL_ACTION_PATTERNS,
}

ACTION_VOCAB_BY_DOMAIN: dict[str, frozenset[str]] = {
    domain: frozenset(action for _, action in patterns)
    for domain, patterns in _ACTION_PATTERNS_BY_DOMAIN.items()
}

ACTION_VOCAB_V0: frozenset[str] = frozenset().union(*ACTION_VOCAB_BY_DOMAIN.values())

PARTY_BY_DOMAIN: dict[str, str] = {
    "tax_appeal": "aab",
}

SHARED_DENIED_RE = re.compile(
    r"\b(?:was\s+)?denied\b|\bdenial\b|\bunable\s+to\b|\bcan(?:no)?t\b.*\bspread\b",
    re.I,
)

TAX_APPEAL_DENIED_RE = re.compile(
    r"invalid\s*[–-]\s*closed",
    re.I,
)

_DENIED_RES_BY_DOMAIN: dict[str, tuple[re.Pattern[str], ...]] = {
    "mortgage_escrow": (SHARED_DENIED_RE,),
    "tax_appeal": (SHARED_DENIED_RE, TAX_APPEAL_DENIED_RE),
}

_GRANTED_RE = re.compile(r"\b(?:was\s+)?granted\b|\bapproved\b", re.I)
_PENDING_RE = re.compile(r"\b(?:pending|opened|requested)\b", re.I)
_REQUEST_RE = re.compile(r"\b(?:request(?:ed|ing)?|ask(?:ed|ing)?)\b", re.I)


def action_patterns_for_domain(
    domain: str | None,
) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Return action regex patterns for one domain, or the union when domain is None."""
    if domain is None:
        merged: list[tuple[re.Pattern[str], str]] = []
        for patterns in _ACTION_PATTERNS_BY_DOMAIN.values():
            merged.extend(patterns)
        return tuple(merged)
    return _ACTION_PATTERNS_BY_DOMAIN.get(domain, ())


def denied_patterns_for_domain(domain: str | None) -> tuple[re.Pattern[str], ...]:
    """Return denied-functor patterns scoped to a domain (shared + domain-specific)."""
    if domain is None:
        seen: set[int] = set()
        merged: list[re.Pattern[str]] = []
        for patterns in _DENIED_RES_BY_DOMAIN.values():
            for pattern in patterns:
                key = id(pattern)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(pattern)
        return tuple(merged)
    return _DENIED_RES_BY_DOMAIN.get(domain, (SHARED_DENIED_RE,))


def functor_patterns_for_domain(
    domain: str | None,
) -> tuple[tuple[re.Pattern[str], str], ...]:
    """Build ordered functor patterns with domain-keyed denied detection."""
    denied = [(pattern, "denied") for pattern in denied_patterns_for_domain(domain)]
    return (
        *denied,
        (_GRANTED_RE, "granted"),
        (_PENDING_RE, "pending"),
        (_REQUEST_RE, "request"),
    )


def _assert_pattern_vocab_consistency() -> None:
    for domain, patterns in _ACTION_PATTERNS_BY_DOMAIN.items():
        derived = {action for _, action in patterns}
        assert derived == ACTION_VOCAB_BY_DOMAIN[domain], domain


def _assert_domain_disjointness() -> None:
    seen: set[str] = set()
    for actions in ACTION_VOCAB_BY_DOMAIN.values():
        overlap = seen & actions
        assert not overlap, f"action vocab overlap: {overlap}"
        seen |= actions


_assert_pattern_vocab_consistency()
_assert_domain_disjointness()
