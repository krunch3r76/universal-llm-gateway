"""Deterministic provenance, volunteered-negative/concession, and purpose-relevance gates."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

FactProvenance = Literal[
    "operator_account",
    "document",
    "intake_answer",
    "prior_draft_only",
]

CheckProfile = Literal["sworn", "correspondence"]

SWORN_CHANNELS = frozenset({"filing", "sworn", "declaration"})

_EXHIBIT_CITE_RE = re.compile(r"\(Exhibit\s+\d+\.?\)", re.I)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an the and or of to in on at for by with from as is are was were be been this that these "
    "those it its i my me we our you your will would can could should may am do does did have has had not no any all "
    "so if then there here which who whom what when where how per via re".split()
)
_CORRESPONDENCE_GENERIC = frozenset(
    {
        "email",
        "attach",
        "attached",
        "attachment",
        "attachments",
        "please",
        "confirm",
        "receipt",
        "thank",
        "thanks",
        "hello",
        "mail",
        "message",
        "kindly",
        "regard",
        "reply",
        "writing",
    }
)
_PURPOSE_MIN_OVERLAP = 1
_NUMBERED_CLAUSE = re.compile(r"\(\s*(\d+)\s*\)")

_VOLUNTEERED_NEGATIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bI\s+(?:do\s+not|don't|no\s+longer)\s+have\s+(?:any\s+)?"
            r"(?:phone\s+records|call\s+records|records\s+of\s+these\s+calls|"
            r"call\s+logs|the\s+envelope|an?\s+envelope)\b",
            re.I,
        ),
        "volunteered_negative",
    ),
    (
        re.compile(r"\bno\s+(?:phone\s+records|call\s+records|envelope)\b", re.I),
        "volunteered_negative",
    ),
    (
        re.compile(
            r"\bI\s+(?:do\s+not|don't|cannot|can't)\s+(?:know|recall|remember)\s+"
            r"(?:the\s+(?:exact\s+)?date|when)\s+(?:I\s+received|it\s+arrived)\b",
            re.I,
        ),
        "volunteered_negative",
    ),
)

_VOLUNTEERED_CONCESSION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bI\s+received\s+the\s+[^.]+?\b(?:notice|bill|letter|assessment)\b"
            r"[^.]+?\b(?:close\s+to(?:\s*/\s*shortly\s+after)?|shortly\s+after|"
            r"soon\s+after|around|near)\s+(?:its|the)\s+date\b",
            re.I,
        ),
        "volunteered_concession",
    ),
)

_DECLARATION_REGISTER = re.compile(
    r"^\s*I,\s+[^,\n]{2,80},\s+(?:am|do|hereby|state|declare|submit|certify)\b"
    r"|\bdeclare\s+under\s+penalty\s+of\s+perjury\b",
    re.I,
)

@dataclass(frozen=True, slots=True)
class PurposePoint:
    point_id: str
    text: str
    anchor_tokens: frozenset[str]
    allows_negative_or_concession: bool = False


@dataclass(frozen=True, slots=True)
class ClaimEvaluation:
    load_bearing: bool
    reason: str | None
    provenance: FactProvenance
    purpose_point: str | None


def check_profile_for_channel(channel: str | None) -> CheckProfile:
    """Sworn profile for filing/sworn/declaration channels; else correspondence."""
    return "sworn" if channel in SWORN_CHANNELS else "correspondence"


def check_class_for_channel(channel: str | None) -> CheckProfile:
    """Alias retained for pipeline handlers."""
    return check_profile_for_channel(channel)


def purpose_content_tokens(text: str) -> frozenset[str]:
    scrubbed = re.sub(r"\S+@\S+", " ", text.lower())
    tokens: set[str] = set()
    for match in _TOKEN_RE.finditer(scrubbed):
        token = match.group(0)
        if len(token) < 2 or token in _STOPWORDS or token in _CORRESPONDENCE_GENERIC:
            continue
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        if token in _STOPWORDS or token in _CORRESPONDENCE_GENERIC:
            continue
        tokens.add(token)
    if "leave" in tokens:
        tokens.add("away")
    return frozenset(tokens)


def _allows_negative(text: str) -> bool:
    lower = text.lower()
    markers = (
        "phone records",
        "do not know the date i received",
        "close to its date",
        "shortly after its date",
    )
    return any(marker in lower for marker in markers)


def _split_numbered_purpose(purpose: str) -> list[str]:
    text = purpose.strip()
    match = _NUMBERED_CLAUSE.search(text)
    if not match:
        return []
    start = match.start()
    body = text[start:]
    parts = re.split(r"\(\s*\d+\s*\)", body)
    points: list[str] = []
    for part in parts:
        cleaned = part.strip(" ;.\n")
        if cleaned:
            points.append(cleaned)
    return points


def _is_short_domain_header(purpose: str) -> bool:
    tokens = purpose_content_tokens(purpose)
    return len(tokens) <= 6 and not _NUMBERED_CLAUSE.search(purpose)


def parse_purpose_points(
    purpose: str,
    purpose_points: list[str] | None = None,
) -> list[PurposePoint]:
    explicit = [str(p).strip() for p in (purpose_points or []) if str(p).strip()]
    if explicit:
        return [
            PurposePoint(
                point_id=f"purpose[{index}]",
                text=item,
                anchor_tokens=purpose_content_tokens(item),
                allows_negative_or_concession=_allows_negative(item),
            )
            for index, item in enumerate(explicit)
        ]
    numbered = _split_numbered_purpose(purpose)
    if numbered:
        return [
            PurposePoint(
                point_id=f"purpose[{index}]",
                text=item,
                anchor_tokens=purpose_content_tokens(item),
                allows_negative_or_concession=_allows_negative(item),
            )
            for index, item in enumerate(numbered)
        ]
    if ";" in purpose:
        segments = [seg.strip() for seg in purpose.split(";") if seg.strip()]
        if len(segments) >= 2:
            return [
                PurposePoint(
                    point_id=f"purpose[{index}]",
                    text=item,
                    anchor_tokens=purpose_content_tokens(item),
                    allows_negative_or_concession=_allows_negative(item),
                )
                for index, item in enumerate(segments)
            ]
    tokens = purpose_content_tokens(purpose)
    if not tokens:
        return []
    return [
        PurposePoint(
            point_id="purpose[0]",
            text=purpose.strip(),
            anchor_tokens=tokens,
            allows_negative_or_concession=_allows_negative(purpose),
        )
    ]


def _coerce_purpose_points(
    purpose_points: Sequence[str] | Sequence[PurposePoint],
) -> list[PurposePoint]:
    if not purpose_points:
        return []
    first = purpose_points[0]
    if isinstance(first, PurposePoint):
        return list(purpose_points)  # type: ignore[arg-type]
    return [
        PurposePoint(
            point_id=f"purpose[{index}]",
            text=str(item),
            anchor_tokens=purpose_content_tokens(str(item)),
            allows_negative_or_concession=_allows_negative(str(item)),
        )
        for index, item in enumerate(purpose_points)
    ]


def map_claim_to_purpose(
    claim: str,
    purpose_points: Sequence[str] | Sequence[PurposePoint],
) -> str | None:
    points = _coerce_purpose_points(purpose_points)
    if not points:
        return None
    claim_tokens = purpose_content_tokens(claim)
    best_id: str | None = None
    best_score = 0
    for point in points:
        score = len(claim_tokens & point.anchor_tokens)
        if score > best_score:
            best_score = score
            best_id = point.point_id
    if best_score >= _PURPOSE_MIN_OVERLAP:
        return best_id
    return None


def is_declaration_register(sentence: str) -> bool:
    return bool(_DECLARATION_REGISTER.search(sentence))


def detect_volunteered_negative_or_concession(
    claim: str,
    purpose_points: list[PurposePoint],
) -> tuple[str | None, str | None]:
    allows = any(p.allows_negative_or_concession for p in purpose_points)
    if allows:
        return None, None
    for pattern, reason in _VOLUNTEERED_NEGATIVE_PATTERNS:
        if pattern.search(claim):
            return reason, None
    for pattern, reason in _VOLUNTEERED_CONCESSION_PATTERNS:
        if pattern.search(claim):
            return reason, None
    return None, None


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _matches_note(claim: str, note: str) -> bool:
    c = _normalize_for_match(claim)
    n = _normalize_for_match(note)
    if c == n or c in n or n in c:
        return True
    c_tokens = purpose_content_tokens(claim)
    n_tokens = purpose_content_tokens(note)
    if not c_tokens or not n_tokens:
        return False
    overlap = len(c_tokens & n_tokens)
    return overlap >= min(3, len(c_tokens), len(n_tokens))


def resolve_claim_provenance(
    claim: str,
    *,
    verbatim_notes: list[str],
    attested_sources: list[str],
    intake_answers: list[str],
    fact_sources: list[dict[str, Any]],
    strict_attestation: bool,
    purpose_points: list[PurposePoint],
) -> FactProvenance:
    """Classify claim provenance from caller-supplied pins and intake answers.

    ``strict_attestation`` is retained as a deprecated no-op keyword: unbacked
    prior-draft text is always ``prior_draft_only`` (never promoted).
    """
    del strict_attestation, purpose_points
    for row in fact_sources:
        if not isinstance(row, dict):
            continue
        pattern = str(row.get("claim") or row.get("match") or "")
        prov = row.get("provenance")
        if pattern and prov in {
            "operator_account",
            "document",
            "intake_answer",
            "prior_draft_only",
        }:
            if pattern in claim or _matches_note(claim, pattern):
                return prov  # type: ignore[return-value]
    for answer in intake_answers:
        if _matches_note(claim, answer):
            return "intake_answer"
    for src in attested_sources:
        if _matches_note(claim, src):
            return "document"
    if _EXHIBIT_CITE_RE.search(claim):
        for answer in intake_answers:
            if _matches_note(claim, answer):
                return "intake_answer"
    for note in verbatim_notes:
        if _matches_note(claim, note):
            return "operator_account"
    if _EXHIBIT_CITE_RE.search(claim):
        return "document"
    return "prior_draft_only"


def evaluate_candidate_claim(
    claim: str,
    *,
    purpose_points: list[PurposePoint],
    verbatim_notes: list[str],
    attested_sources: list[str],
    intake_answers: list[str],
    fact_sources: list[dict[str, Any]],
    strict_attestation: bool,
) -> ClaimEvaluation:
    """Evaluate load-bearing status; ``strict_attestation`` is deprecated (no-op)."""
    neg_reason, _ = detect_volunteered_negative_or_concession(claim, purpose_points)
    prov = resolve_claim_provenance(
        claim,
        verbatim_notes=verbatim_notes,
        attested_sources=attested_sources,
        intake_answers=intake_answers,
        fact_sources=fact_sources,
        strict_attestation=strict_attestation,
        purpose_points=purpose_points,
    )
    if neg_reason:
        return ClaimEvaluation(False, neg_reason, prov, None)
    if prov == "intake_answer":
        return ClaimEvaluation(False, "intake_answer_context_only", prov, None)
    point_id = map_claim_to_purpose(claim, purpose_points)
    if prov == "prior_draft_only":
        if point_id is None:
            return ClaimEvaluation(
                False, "prior_draft_not_attested", prov, None
            )
        return ClaimEvaluation(True, None, prov, point_id)
    if point_id is None:
        return ClaimEvaluation(False, "not_purpose_relevant", prov, None)
    return ClaimEvaluation(True, None, prov, point_id)
