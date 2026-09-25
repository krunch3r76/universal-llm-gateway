"""Unified review verdict grammar for R-admit, gate-6 harvest, and CDP review bodies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Merits verdict tokens (closed set). Hyphen spellings normalize before classify.
_TOKEN_PATTERN = (
    r"ADMIT_WITH_AMENDMENTS|ADMIT|"
    r"RATIFY_WITH_CONDITIONS|RATIFY-WITH-CONDITIONS|RATIFY|"
    r"REJECT|RETURN|SCOPE-DRIFT|SCOPE_DRIFT"
)
_MERITS_RE = re.compile(
    rf"\*{{0,2}}\b(?:Merits(?:\s+(?:verdict|disposition))?|merits)\b\*{{0,2}}"
    rf"\s*[:\-—]\s*\*{{0,2}}\s*({_TOKEN_PATTERN})\b",
    re.IGNORECASE,
)
_SUBJECT_TOKEN_RE = re.compile(rf"\b({_TOKEN_PATTERN})\b")
_SCOPE_CHECK_PREFIX_RE = re.compile(r"(?i)scope\s*check")
_GATE6_HEADING_VERDICT_RE = re.compile(
    r"^##\s*Verdict:\s*\*\*(?P<token>[^*]+)\*\*",
    re.IGNORECASE | re.MULTILINE,
)
_GATE6_BOLD_VERDICT_RE = re.compile(
    r"^\*\*Verdict:\*\*\s*\*\*(?P<token>[^*]+)\*\*",
    re.IGNORECASE | re.MULTILINE,
)
_ADVANCE = frozenset({"ADMIT", "RATIFY"})
_AMEND = frozenset({"ADMIT_WITH_AMENDMENTS", "RATIFY_WITH_CONDITIONS"})
_BLOCK = frozenset({"RETURN", "SCOPE-DRIFT", "SCOPE_DRIFT", "REJECT"})


class VerdictAction(StrEnum):
    """Machine action after parsing a review harvest or sidecar body."""

    ADVANCE = "advance"
    AMENDMENTS_REQUIRED = "amendments_required"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ParsedVerdict:
    """Outcome of parsing one review body for a verdict token."""

    token: str | None
    action: VerdictAction
    reason: str


def normalize_verdict_token(raw: str) -> str:
    """Uppercase and map hyphen aliases to underscore forms before classify."""
    token = raw.strip().upper()
    token = token.replace("RATIFY-WITH-CONDITIONS", "RATIFY_WITH_CONDITIONS")
    token = token.replace("SCOPE_DRIFT", "SCOPE-DRIFT")
    return token


def _classify(normalized: str) -> ParsedVerdict:
    if normalized in _ADVANCE:
        return ParsedVerdict(normalized, VerdictAction.ADVANCE, "advance_ok")
    if normalized in _AMEND:
        return ParsedVerdict(
            normalized,
            VerdictAction.AMENDMENTS_REQUIRED,
            "amendments_must_fold_before_implement",
        )
    if normalized in _BLOCK:
        return ParsedVerdict(normalized, VerdictAction.BLOCKED, "r_verdict_blocked")
    return ParsedVerdict(normalized, VerdictAction.BLOCKED, "unknown_verdict")


def _token_in_scope_check_context(body: str, start: int) -> bool:
    prefix = body[max(0, start - 64) : start]
    return _SCOPE_CHECK_PREFIX_RE.search(prefix) is not None


def _parse_token_from_raw(raw: str) -> ParsedVerdict:
    normalized = normalize_verdict_token(raw.split()[0] if raw else "")
    if not normalized:
        return ParsedVerdict(None, VerdictAction.BLOCKED, "unparseable_r_verdict")
    if normalized.startswith("REJECT"):
        return ParsedVerdict("REJECT", VerdictAction.BLOCKED, "r_verdict_blocked")
    return _classify(normalized)


def parse_merits_line(text: str) -> ParsedVerdict:
    """Extract the merits verdict from R sidecar/harvest text; fail closed."""
    body = text or ""
    match = _MERITS_RE.search(body)
    if match is not None:
        return _parse_token_from_raw(match.group(1))
    for m in _SUBJECT_TOKEN_RE.finditer(body):
        if _token_in_scope_check_context(body, m.start()):
            continue
        parsed = _parse_token_from_raw(m.group(1))
        if parsed.token is not None:
            return parsed
    return ParsedVerdict(None, VerdictAction.BLOCKED, "unparseable_r_verdict")


def parse_gate6_markdown(text: str) -> ParsedVerdict:
    """Parse gate-6 ``## Verdict:`` / ``**Verdict:**`` markdown blocks."""
    body = text or ""
    for pattern in (_GATE6_HEADING_VERDICT_RE, _GATE6_BOLD_VERDICT_RE):
        match = pattern.search(body)
        if match is not None:
            return _parse_token_from_raw(match.group("token"))
    return ParsedVerdict(None, VerdictAction.BLOCKED, "unparseable_gate6_verdict")


def parse_any_review_body(text: str) -> ParsedVerdict:
    """Prefer explicit Merits line; fall back to gate-6 Verdict block."""
    merits = parse_merits_line(text)
    if merits.token is not None:
        return merits
    return parse_gate6_markdown(text)


def format_canonical_merits_line(token: str) -> str:
    """Single line for R-admit / R1: ``Merits: RATIFY`` (underscore tokens)."""
    normalized = normalize_verdict_token(token)
    display = normalized.replace("SCOPE-DRIFT", "SCOPE_DRIFT")
    return f"Merits: {display}"


def format_canonical_gate6_block(token: str) -> str:
    """Markdown block gate-6 consumers already accept."""
    normalized = normalize_verdict_token(token)
    if normalized == "RATIFY_WITH_CONDITIONS":
        display = "RATIFY-WITH-CONDITIONS"
    else:
        display = normalized
    return f"**Verdict:** **{display}**"


def gate6_affirmative_disposition(body: str) -> bool:
    """True only when parsed gate-6 action is ADVANCE (missing token fails closed)."""
    parsed = parse_gate6_markdown(body)
    return parsed.token is not None and parsed.action is VerdictAction.ADVANCE


__all__ = [
    "ParsedVerdict",
    "VerdictAction",
    "format_canonical_gate6_block",
    "format_canonical_merits_line",
    "gate6_affirmative_disposition",
    "normalize_verdict_token",
    "parse_any_review_body",
    "parse_gate6_markdown",
    "parse_merits_line",
]
