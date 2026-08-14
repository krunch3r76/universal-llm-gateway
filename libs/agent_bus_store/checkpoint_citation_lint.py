"""CHECKPOINT body lint: ambiguous falsifier refs and citation token extraction.

Qualification rule (namespace disambiguation): a numbered falsifier reference is
**not** flagged when it is bound to an explicit namespace prefix. Prefixes live in
``FALSIFIER_NAMESPACE_PREFIXES`` (extensible frozenset — add a slug, not matcher
logic). Binding forms: ``{prefix} F{n}``, ``{prefix}:F{n}``, or
``{prefix} [§section] falsifier(s) {n}``. Colon-bound ``F{n}`` and hyphenated
slugs (``CCA-1``, ``CCL-0``) never hit the bare-``F``+digit rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

FALSIFIER_NAMESPACE_PREFIXES: frozenset[str] = frozenset(
    {"envelope", "attribution", "attr"}
)

_BARE_F_DIGIT = re.compile(r"(?<![A-Za-z0-9-])(F\d+)(?![A-Za-z0-9-])")
_FALSIFIER_NUMBER = re.compile(
    r"\bfalsifiers?\s+(\d+(?:\s*(?:and|\+)\s*\d+)*)",
    re.IGNORECASE,
)
_NS_PREFIX_ALT = "|".join(re.escape(p) for p in sorted(FALSIFIER_NAMESPACE_PREFIXES))
_QUALIFIED_F_SPACE = re.compile(rf"\b(?:{_NS_PREFIX_ALT})\s+(F\d+)\b", re.IGNORECASE)
_QUALIFIED_F_COLON = re.compile(rf"\b(?:{_NS_PREFIX_ALT}):(F\d+)\b", re.IGNORECASE)
_QUALIFIED_FALSIFIER = re.compile(
    rf"\b(?:{_NS_PREFIX_ALT})(?:\s§[\w.]+\s+|\s+)falsifiers?\s+\d",
    re.IGNORECASE,
)

_CITATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("assertion", re.compile(r"\ba:(\d+)\b")),
    ("todo", re.compile(r"\btodo:([a-z0-9][a-z0-9_-]*)\b", re.IGNORECASE)),
    ("task", re.compile(r"\btask:([a-z0-9][a-z0-9_-]*)\b", re.IGNORECASE)),
    ("decision", re.compile(r"\bdecision:([a-z0-9][a-z0-9_-]*)\b", re.IGNORECASE)),
    ("plan", re.compile(r"\bplan:([a-z0-9][a-z0-9_-]*)\b", re.IGNORECASE)),
    ("agent_bus", re.compile(r"\bagent-bus:(\d+)\b", re.IGNORECASE)),
)

_AGENT_BUS_ID = re.compile(r"\bagent-bus:(\d+)\b", re.IGNORECASE)
_LANE_ROLE_CLAUSE = re.compile(
    r"\(\s*(sub_mission|hop|spillover|dispatch|side|parallel)\s+of\s+\d+\s*\)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AmbiguousFalsifierRef:
    raw: str
    kind: str
    offset: int


@dataclass(frozen=True, slots=True)
class CitationToken:
    raw: str
    kind: str
    identifier: str
    offset: int


@dataclass(frozen=True, slots=True)
class CitationResolution:
    kind: str
    identifier: str
    resolved: bool


class CitationResolver(Protocol):
    def __call__(self, token: CitationToken) -> CitationResolution | None: ...


@dataclass(frozen=True, slots=True)
class LaneCitationAdvisory:
    raw: str
    thread_id: str
    offset: int


@dataclass(frozen=True, slots=True)
class CheckpointCitationFindings:
    ambiguous_refs: tuple[AmbiguousFalsifierRef, ...]
    citation_tokens: tuple[CitationToken, ...]
    lane_citation_advisories: tuple[LaneCitationAdvisory, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.ambiguous_refs


def _qualified_f_spans(body: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pattern in (_QUALIFIED_F_SPACE, _QUALIFIED_F_COLON):
        for match in pattern.finditer(body):
            spans.append(match.span(1))
    return spans


def _qualified_falsifier_spans(body: str) -> list[tuple[int, int]]:
    return [match.span() for match in _QUALIFIED_FALSIFIER.finditer(body)]


def _span_overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(q_start <= start and end <= q_end for q_start, q_end in spans)


def _find_ambiguous_f_refs(body: str) -> list[AmbiguousFalsifierRef]:
    qualified = _qualified_f_spans(body)
    findings: list[AmbiguousFalsifierRef] = []
    for match in _BARE_F_DIGIT.finditer(body):
        start, end = match.span(1)
        if _span_overlaps(start, end, qualified):
            continue
        findings.append(
            AmbiguousFalsifierRef(
                raw=match.group(1),
                kind="bare_f_digit",
                offset=start,
            )
        )
    return findings


def _find_ambiguous_falsifier_refs(body: str) -> list[AmbiguousFalsifierRef]:
    qualified = _qualified_falsifier_spans(body)
    findings: list[AmbiguousFalsifierRef] = []
    for match in _FALSIFIER_NUMBER.finditer(body):
        if _span_overlaps(match.start(), match.end(), qualified):
            continue
        findings.append(
            AmbiguousFalsifierRef(
                raw=match.group(0),
                kind="falsifier_number",
                offset=match.start(),
            )
        )
    return findings


def _extract_citation_tokens(body: str) -> tuple[CitationToken, ...]:
    candidates: list[CitationToken] = []
    for kind, pattern in _CITATION_PATTERNS:
        for match in pattern.finditer(body):
            candidates.append(
                CitationToken(
                    raw=match.group(0),
                    kind=kind,
                    identifier=match.group(1),
                    offset=match.start(),
                )
            )
    candidates.sort(key=lambda token: token.offset)
    seen: set[tuple[str, str]] = set()
    ordered: list[CitationToken] = []
    for token in candidates:
        key = (token.kind, token.identifier)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(token)
    return tuple(ordered)


def _find_lane_citation_advisories(body: str) -> tuple[LaneCitationAdvisory, ...]:
    findings: list[LaneCitationAdvisory] = []
    for match in _AGENT_BUS_ID.finditer(body):
        start = match.start()
        window = body[max(0, start - 80) : start + len(match.group(0)) + 80]
        if _LANE_ROLE_CLAUSE.search(window):
            continue
        findings.append(
            LaneCitationAdvisory(
                raw=match.group(0),
                thread_id=match.group(1),
                offset=start,
            )
        )
    return tuple(findings)


def lint_checkpoint_citations(
    body: str,
    *,
    subject: str | None = None,
) -> CheckpointCitationFindings:
    """Lint a CHECKPOINT turn body for ambiguous falsifiers and citation tokens."""
    del subject  # reserved for a later gate; lint is body-driven in this leg
    ambiguous = tuple(
        _find_ambiguous_f_refs(body) + _find_ambiguous_falsifier_refs(body)
    )
    citations = _extract_citation_tokens(body)
    lane_advisories = _find_lane_citation_advisories(body)
    return CheckpointCitationFindings(
        ambiguous_refs=ambiguous,
        citation_tokens=citations,
        lane_citation_advisories=lane_advisories,
    )


__all__ = [
    "AmbiguousFalsifierRef",
    "CheckpointCitationFindings",
    "CitationResolution",
    "CitationResolver",
    "CitationToken",
    "FALSIFIER_NAMESPACE_PREFIXES",
    "LaneCitationAdvisory",
    "lint_checkpoint_citations",
]
