"""Fail-closed R-admit verdict gate for autonomous path-sim arcs.

The background lead both fires and consumes R-admit verdicts. Substrate separation
covers who *reasons* the review; this module covers *enforcement*: only explicit
advance verdicts may proceed to implement. RETURN, SCOPE-DRIFT, and unparseable
text fail closed (BLOCKED) so autonomous cannot slide back into self-certify.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class RGateAction(StrEnum):
    """Machine action after parsing an R-admit sidecar or harvest body."""

    ADVANCE = "advance"
    AMENDMENTS_REQUIRED = "amendments_required"
    BLOCKED = "blocked"


# Merits verdict tokens the path-sim R seat may emit (case-insensitive match).
_ADVANCE = frozenset({"ADMIT", "RATIFY"})
_AMEND = frozenset({"ADMIT_WITH_AMENDMENTS", "RATIFY_WITH_CONDITIONS"})
_BLOCK = frozenset({"RETURN", "SCOPE-DRIFT", "SCOPE_DRIFT"})
_VERDICT_RE = re.compile(
    r"\b(?:Merits(?:\s+verdict)?|merits)\s*[:\-—]\s*"
    r"(ADMIT_WITH_AMENDMENTS|ADMIT|RATIFY_WITH_CONDITIONS|RATIFY|RETURN|SCOPE-DRIFT|SCOPE_DRIFT)\b",
    re.IGNORECASE,
)
_SUBJECT_VERDICT_RE = re.compile(
    r"\b(ADMIT_WITH_AMENDMENTS|ADMIT|RATIFY_WITH_CONDITIONS|RATIFY|RETURN|SCOPE-DRIFT|SCOPE_DRIFT)\b"
)


@dataclass(frozen=True)
class ParsedRVerdict:
    """Outcome of parsing one R-admit harvest or sidecar body."""

    verdict: str | None
    action: RGateAction
    reason: str


def _normalize_verdict(raw: str) -> str:
    return raw.upper().replace("SCOPE_DRIFT", "SCOPE-DRIFT")


def parse_r_verdict(text: str) -> ParsedRVerdict:
    """Extract the merits verdict from R sidecar/harvest text; fail closed."""
    body = text or ""
    match = _VERDICT_RE.search(body)
    if match is None:
        # Subject-line fallback: "G5 R-admit verdict — ADMIT_WITH_AMENDMENTS"
        for m in _SUBJECT_VERDICT_RE.finditer(body):
            token = _normalize_verdict(m.group(1))
            if token in _ADVANCE | _AMEND | _BLOCK:
                return _classify(token)
        return ParsedRVerdict(
            None,
            RGateAction.BLOCKED,
            "unparseable_r_verdict",
        )
    return _classify(_normalize_verdict(match.group(1)))


def _classify(verdict: str) -> ParsedRVerdict:
    if verdict in _ADVANCE:
        return ParsedRVerdict(verdict, RGateAction.ADVANCE, "advance_ok")
    if verdict in _AMEND:
        return ParsedRVerdict(
            verdict,
            RGateAction.AMENDMENTS_REQUIRED,
            "amendments_must_fold_before_implement",
        )
    if verdict in _BLOCK:
        return ParsedRVerdict(verdict, RGateAction.BLOCKED, "r_verdict_blocked")
    return ParsedRVerdict(verdict, RGateAction.BLOCKED, "unknown_verdict")


def advance_allowed(text: str) -> bool:
    """True only when the parsed merits verdict is ADMIT or RATIFY."""
    return parse_r_verdict(text).action is RGateAction.ADVANCE


def gate_action(text: str) -> RGateAction:
    """Return the machine gate action for an R-admit harvest or sidecar body."""
    return parse_r_verdict(text).action
