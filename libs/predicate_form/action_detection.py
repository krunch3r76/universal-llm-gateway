"""Local clause/sentence matching for action enrichment (arc 6386 slice 5b-fix).

Segments claims before functor/action pairing so negated or cross-clause matches
do not combine. Prefer silence over a wrong predicate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .action_vocabulary import TERMINAL_FUNCTORS

_ACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
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

_DENIED_RE = re.compile(
    r"\b(?:was\s+)?denied\b|\bunable\s+to\b|\bcan(?:no)?t\b.*\bspread\b",
    re.I,
)
_GRANTED_RE = re.compile(r"\b(?:was\s+)?granted\b|\bapproved\b", re.I)
_PENDING_RE = re.compile(r"\b(?:pending|opened|requested)\b", re.I)
_REQUEST_RE = re.compile(r"\b(?:request(?:ed|ing)?|ask(?:ed|ing)?)\b", re.I)

_DATE_RE = re.compile(
    r"\b(20\d{2}-\d{2}-\d{2})\b|"
    r"\b(?:on\s+)?(?:the\s+)?(\d{4}-\d{2}-\d{2})\b|"
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
)
_MONTH_DATE_RE = re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+(\d{1,2}),?\s+(20\d{2})\b",
    re.I,
)
_WO_RE = re.compile(r"\bWO\s*#?\s*(\d+)\b", re.I)

_FUNCTOR_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_DENIED_RE, "denied"),
    (_GRANTED_RE, "granted"),
    (_PENDING_RE, "pending"),
    (_REQUEST_RE, "request"),
)

_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|nor)\b|"
    r"\bno\s+guarantee\b|"
    r"\bshould\s+not\s+be\s+read\s+as\b|"
    r"\bcannot\s+put\s+it\s+in\s+writing\b|"
    r"\bdo\s+not\b|\bdon't\b|\bdoesn't\b|\bdidn't\b|\bwasn't\b|\bweren't\b",
    re.I,
)
_HYPOTHETICAL_RE = re.compile(r"\b(?:would|if|might|could|may)\b", re.I)

_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.;!?])\s+|\n+")

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

CLAIM_EXCERPT_MAX = 200


@dataclass(frozen=True)
class SegmentMatch:
    segment: str
    action: str
    functor: str
    date: str | None
    wo_id: str | None


def split_segments(claim: str) -> list[str]:
    parts = _SEGMENT_SPLIT_RE.split(claim.strip())
    return [part.strip() for part in parts if part.strip()]


def truncate_claim_excerpt(text: str, *, limit: int = CLAIM_EXCERPT_MAX) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _match_in_negation_scope(segment: str, match: re.Match[str]) -> bool:
    start = match.start()
    window_start = max(0, start - 100)
    prefix = segment[window_start:start]
    if not _NEGATION_RE.search(prefix):
        return False
    between = prefix[_NEGATION_RE.search(prefix).end() :]  # type: ignore[union-attr]
    return not _HYPOTHETICAL_RE.search(between)


def _detect_action(segment: str) -> str | None:
    for pattern, action in _ACTION_PATTERNS:
        if pattern.search(segment):
            return action
    return None


def _detect_functor(segment: str) -> str | None:
    for pattern, functor in _FUNCTOR_PATTERNS:
        match = pattern.search(segment)
        if match is None:
            continue
        if _match_in_negation_scope(segment, match):
            continue
        return functor
    return None


def _normalize_date_group(group: str) -> str | None:
    if "/" in group:
        parts = group.split("/")
        if len(parts) == 3:
            month, day, year = parts
            return f"{year}-{int(month):02d}-{int(day):02d}"
        return None
    if len(group) >= 10:
        return group[:10]
    return group


def _parse_month_date(segment: str) -> str | None:
    match = _MONTH_DATE_RE.search(segment)
    if match is None:
        return None
    month_token, day, year = match.groups()
    month = _MONTHS.get(month_token.lower())
    if month is None:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


def detect_date_in_segment(segment: str, valid_from: str | None = None) -> str | None:
    if valid_from:
        text = valid_from.strip()
        if text:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            if len(text) >= 10 and text[4] == "-" and text[7] == "-":
                return text[:10]
    for match in _DATE_RE.finditer(segment):
        for group in match.groups():
            if not group:
                continue
            normalized = _normalize_date_group(group)
            if normalized:
                return normalized
    return _parse_month_date(segment)


def _segment_match(segment: str, valid_from: str | None) -> SegmentMatch | None:
    action = _detect_action(segment)
    functor = _detect_functor(segment)
    if not action or not functor:
        return None
    date = detect_date_in_segment(segment, valid_from)
    wo_match = _WO_RE.search(segment)
    wo_id = wo_match.group(1) if wo_match else None
    return SegmentMatch(
        segment=segment,
        action=action,
        functor=functor,
        date=date,
        wo_id=wo_id,
    )


def _rank_key(match: SegmentMatch) -> tuple[int, int, int]:
    terminal_rank = 2 if match.functor in TERMINAL_FUNCTORS else 1
    date_rank = 1 if match.date else 0
    return (terminal_rank, date_rank, len(match.segment))


def match_claim_segments(claim: str, *, valid_from: str | None = None) -> SegmentMatch | None:
    """Return the best local segment match, or None when ambiguous."""
    candidates = [
        matched
        for segment in split_segments(claim)
        for matched in [_segment_match(segment, valid_from)]
        if matched is not None
    ]
    if not candidates:
        return None

    terminal = [c for c in candidates if c.functor in TERMINAL_FUNCTORS]
    pool = terminal or candidates
    if len(pool) > 1:
        keys = {(c.functor, c.action) for c in pool}
        if len(keys) > 1:
            if terminal and len({c.functor for c in terminal}) > 1:
                return None
            pool = terminal or pool

    return max(pool, key=_rank_key)
