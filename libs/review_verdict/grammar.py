"""Unified review verdict grammar for R-admit, gate-6 harvest, and CDP review bodies."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Merits verdict tokens (closed set). Hyphen spellings normalize before classify.
# RETURN_TO_DESIGN precedes RETURN so the longer prompt-vocab token wins (a:37226).
_TOKEN_PATTERN = (
    r"ADMIT_WITH_AMENDMENTS|ADMIT|"
    r"RATIFY_WITH_CONDITIONS|RATIFY-WITH-CONDITIONS|RATIFY|"
    r"REJECT|RETURN_TO_DESIGN|RETURN-TO-DESIGN|RETURN|SCOPE-DRIFT|SCOPE_DRIFT"
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
# Trailing prose after a block token (space/tab/em-dash/hyphen/paren/colon).
_BLOCK_TRAILING_SEP = " \t—-(:"
# First-word aliases only. ADMIT and RATIFY stay whole-string members of _ADVANCE.
_CLOSED_ADVANCE_ALIASES = frozenset({"PASS", "APPROVE", "SHIP"})
_STANDALONE_VERDICT_RE = re.compile(
    r"(?m)^VERDICT:\s*(?P<raw>.+?)\s*$",
    re.IGNORECASE,
)


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
    """Uppercase and map hyphen aliases to underscore forms before classify.

    Prompt vocabulary ``RETURN_TO_DESIGN`` (cheap-recon / code-review) collapses
    to canonical ``RETURN`` so harvest and R-admit share one BLOCK token (a:37226).
    """
    token = raw.strip().upper()
    token = token.replace("RATIFY-WITH-CONDITIONS", "RATIFY_WITH_CONDITIONS")
    token = token.replace("SCOPE_DRIFT", "SCOPE-DRIFT")
    token = token.replace("RETURN-TO-DESIGN", "RETURN_TO_DESIGN")
    if token == "RETURN_TO_DESIGN":
        return "RETURN"
    if token.startswith("RETURN_TO_DESIGN"):
        rest = token[len("RETURN_TO_DESIGN") :]
        if rest and rest[0] in _BLOCK_TRAILING_SEP:
            return "RETURN" + rest
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


def _block_token_with_trailing(normalized: str) -> ParsedVerdict | None:
    """Block tokens may carry trailing prose; closed-set tokens must match whole string."""
    for token in ("REJECT", "RETURN", "SCOPE-DRIFT"):
        if normalized == token or normalized.startswith(f"{token} "):
            return ParsedVerdict(token, VerdictAction.BLOCKED, "r_verdict_blocked")
        if normalized.startswith(token) and len(normalized) > len(token):
            tail = normalized[len(token) :]
            if tail and tail[0] in _BLOCK_TRAILING_SEP:
                return ParsedVerdict(token, VerdictAction.BLOCKED, "r_verdict_blocked")
    return None


def _closed_advance_alias(raw: str) -> ParsedVerdict | None:
    """Advance when the first word is PASS, APPROVE, or SHIP.

    The word may end with one period and then further prose (``pass. B1 is
    closed.``). A further word with no period (``pass with conditions``) is
    not an advance. ADMIT and RATIFY are not aliases here.
    """
    parts = raw.split()
    if not parts:
        return None
    first = parts[0]
    ended_with_period = first.endswith(".")
    word = first[:-1] if ended_with_period else first
    normalized = word.upper()
    if normalized not in _CLOSED_ADVANCE_ALIASES:
        return None
    if len(parts) > 1 and not ended_with_period:
        return None
    return ParsedVerdict(normalized, VerdictAction.ADVANCE, "advance_ok")


def _parse_token_from_raw(raw: str) -> ParsedVerdict:
    stripped = (raw or "").strip()
    if not stripped:
        return ParsedVerdict(None, VerdictAction.BLOCKED, "unparseable_r_verdict")
    normalized = normalize_verdict_token(stripped)
    blocked = _block_token_with_trailing(normalized)
    if blocked is not None:
        return blocked
    alias = _closed_advance_alias(stripped)
    if alias is not None:
        return alias
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


def _gate6_verdict_priority(parsed: ParsedVerdict) -> int:
    if parsed.action is VerdictAction.BLOCKED:
        return 0
    if parsed.action is VerdictAction.AMENDMENTS_REQUIRED:
        return 1
    return 2


def parse_gate6_markdown(text: str) -> ParsedVerdict:
    """Parse gate-6 ``## Verdict:`` / ``**Verdict:**`` markdown blocks."""
    body = text or ""
    parsed_lines: list[ParsedVerdict] = []
    for pattern in (_GATE6_HEADING_VERDICT_RE, _GATE6_BOLD_VERDICT_RE):
        for match in pattern.finditer(body):
            parsed_lines.append(_parse_token_from_raw(match.group("token")))
    if not parsed_lines:
        return ParsedVerdict(None, VerdictAction.BLOCKED, "unparseable_gate6_verdict")
    return min(parsed_lines, key=_gate6_verdict_priority)


def parse_any_review_body(text: str) -> ParsedVerdict:
    """Prefer Merits, then a gate-6 block, then a whole-line ``VERDICT:``."""
    merits = parse_merits_line(text)
    if merits.token is not None:
        return merits
    gate6 = parse_gate6_markdown(text)
    if gate6.token is not None:
        return gate6
    body = text or ""
    for match in _STANDALONE_VERDICT_RE.finditer(body):
        parsed = _parse_token_from_raw(match.group("raw"))
        if parsed.token is not None:
            return parsed
    return gate6


def has_parseable_verdict(text: str) -> bool:
    """True when any accepted review-verdict grammar yields a token.

    Used as the harvest/proof gate for ``job=delivery-review`` so skill-induction
    acks and mid-tool prose cannot seal a CDP review (a:37156 / a:37034).
    """
    return parse_any_review_body(text).token is not None


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
    "has_parseable_verdict",
    "normalize_verdict_token",
    "parse_any_review_body",
    "parse_gate6_markdown",
    "parse_merits_line",
]
