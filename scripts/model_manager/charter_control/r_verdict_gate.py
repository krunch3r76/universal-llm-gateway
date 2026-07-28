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
# Optional markdown emphasis around the Merits label / colon (a:26595 — bold
# **Merits:** otherwise falls through to subject fallback and grabs Scope RATIFY).
_VERDICT_RE = re.compile(
    r"\*{0,2}\b(?:Merits(?:\s+(?:verdict|disposition))?|merits)\b\*{0,2}"
    r"\s*[:\-—]\s*\*{0,2}\s*"
    r"(ADMIT_WITH_AMENDMENTS|ADMIT|RATIFY_WITH_CONDITIONS|RATIFY|RETURN|SCOPE-DRIFT|SCOPE_DRIFT)\b",
    re.IGNORECASE,
)
_SUBJECT_VERDICT_RE = re.compile(
    r"\b(ADMIT_WITH_AMENDMENTS|ADMIT|RATIFY_WITH_CONDITIONS|RATIFY|RETURN|SCOPE-DRIFT|SCOPE_DRIFT)\b"
)
_SCOPE_CHECK_PREFIX_RE = re.compile(r"(?i)scope\s*check")


@dataclass(frozen=True)
class ParsedRVerdict:
    """Outcome of parsing one R-admit harvest or sidecar body."""

    verdict: str | None
    action: RGateAction
    reason: str


def _normalize_verdict(raw: str) -> str:
    return raw.upper().replace("SCOPE_DRIFT", "SCOPE-DRIFT")


def _token_in_scope_check_context(body: str, start: int) -> bool:
    """True when a bare verdict token sits under a Scope-check label (not Merits)."""
    prefix = body[max(0, start - 64) : start]
    return _SCOPE_CHECK_PREFIX_RE.search(prefix) is not None


def parse_r_verdict(text: str) -> ParsedRVerdict:
    """Extract the merits verdict from R sidecar/harvest text; fail closed."""
    body = text or ""
    match = _VERDICT_RE.search(body)
    if match is not None:
        return _classify(_normalize_verdict(match.group(1)))
    # Subject-line fallback: "G5 R-admit verdict — ADMIT_WITH_AMENDMENTS".
    # Skip Scope-check tokens so a leading "Scope check: RATIFY" cannot mask Merits.
    for m in _SUBJECT_VERDICT_RE.finditer(body):
        token = _normalize_verdict(m.group(1))
        if token not in _ADVANCE | _AMEND | _BLOCK:
            continue
        if _token_in_scope_check_context(body, m.start()):
            continue
        return _classify(token)
    return ParsedRVerdict(
        None,
        RGateAction.BLOCKED,
        "unparseable_r_verdict",
    )


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


def parse_r_verdict_with_independence(
    text: str,
    *,
    r_family: str | None = None,
    implement_family: str | None = None,
) -> ParsedRVerdict:
    """Parse R verdict and reject ADVANCE when R family equals implement family."""
    parsed = parse_r_verdict(text)
    if parsed.action is not RGateAction.ADVANCE:
        return parsed
    rf = (r_family or "").strip().lower()
    impl = (implement_family or "").strip().lower()
    if rf and impl and rf == impl:
        return ParsedRVerdict(
            parsed.verdict,
            RGateAction.BLOCKED,
            "same_family_r_pre_check_only",
        )
    return parsed


def advance_allowed_with_independence(
    text: str,
    *,
    r_family: str | None = None,
    implement_family: str | None = None,
) -> bool:
    """True only when merits verdict advances and families differ."""
    return (
        parse_r_verdict_with_independence(
            text,
            r_family=r_family,
            implement_family=implement_family,
        ).action
        is RGateAction.ADVANCE
    )


@dataclass(frozen=True)
class ConsultProvenance:
    """Single ``implement_ready`` consult schema (R-admit and judgment-gap)."""

    consult_thread: str
    verdict: str
    consultant_family: str
    consultant_substrate: str


def consult_provenance_from_r_admit(
    *,
    consult_thread: str,
    harvest_text: str,
    consultant_family: str = "anthropic",
    consultant_substrate: str = "web-anthropic",
) -> ConsultProvenance | None:
    """Map an R-admit harvest into the shared consult provenance schema.

    Holder-fired G3 and consult-seat judgment gaps write the same four fields
    so ``implement_ready`` has one gate (Fable G7 E2). Returns ``None`` when the
    harvest has no parseable path-sim verdict token.
    """
    thread = (consult_thread or "").strip()
    if not thread:
        return None
    parsed = parse_r_verdict(harvest_text)
    if not parsed.verdict:
        return None
    family = (consultant_family or "").strip() or "anthropic"
    substrate = (consultant_substrate or "").strip() or "web-anthropic"
    return ConsultProvenance(
        consult_thread=thread,
        verdict=parsed.verdict,
        consultant_family=family,
        consultant_substrate=substrate,
    )


def format_consult_provenance_md(prov: ConsultProvenance, *, evidence: str | None = None) -> str:
    """CHECKPOINT markdown block for the shared consult provenance schema."""
    lines = [
        "## Consult provenance",
        f"- consult_thread: {prov.consult_thread}",
        f"- verdict: {prov.verdict}",
        f"- consultant_family: {prov.consultant_family}",
        f"- consultant_substrate: {prov.consultant_substrate}",
    ]
    if evidence:
        lines.append(f"- evidence: {evidence}")
    return "\n".join(lines) + "\n"
