"""Fail-closed R-admit verdict gate for autonomous path-sim arcs.

The background lead both fires and consumes R-admit verdicts. Substrate separation
covers who *reasons* the review; this module covers *enforcement*: only explicit
advance verdicts may proceed to implement. RETURN, SCOPE-DRIFT, and unparseable
text fail closed (BLOCKED) so autonomous cannot slide back into self-certify.
"""

from __future__ import annotations

from dataclasses import dataclass

from review_verdict import VerdictAction as RGateAction
from review_verdict import parse_merits_line


@dataclass(frozen=True)
class ParsedRVerdict:
    """Outcome of parsing one R-admit harvest or sidecar body."""

    verdict: str | None
    action: RGateAction
    reason: str


def parse_r_verdict(text: str) -> ParsedRVerdict:
    """Extract the merits verdict from R sidecar/harvest text; fail closed."""
    parsed = parse_merits_line(text)
    return ParsedRVerdict(parsed.token, RGateAction(parsed.action), parsed.reason)


def advance_allowed(text: str) -> bool:
    """True only when the parsed merits verdict is ADMIT or RATIFY."""
    return parse_r_verdict(text).action is RGateAction.ADVANCE


def gate_action(text: str) -> RGateAction:
    """Return the machine gate action for an R-admit harvest or sidecar body."""
    return parse_r_verdict(text).action


@dataclass(frozen=True)
class ConsultProvenance:
    """Single ``implement_ready`` consult schema (R-admit and judgment-gap)."""

    consult_thread: str
    verdict: str
    consultant_model: str
    consultant_effort: str | None
    consultant_substrate: str


_EFFORT_UNMEASURED_TOKEN = "unmeasured"


def consult_provenance_from_r_admit(
    *,
    consult_thread: str,
    harvest_text: str,
    consultant_model: str,
    consultant_effort: str | None,
    consultant_substrate: str,
) -> ConsultProvenance | None:
    """Map an R-admit harvest into the shared consult provenance schema.

    Returns ``None`` when the harvest has no parseable verdict token or when
    model or substrate is empty or model is ``unknown``.
    """
    thread = (consult_thread or "").strip()
    if not thread:
        return None
    parsed = parse_r_verdict(harvest_text)
    if not parsed.verdict:
        return None
    model = (consultant_model or "").strip()
    substrate = (consultant_substrate or "").strip()
    if not model or model == "unknown" or not substrate:
        return None
    return ConsultProvenance(
        consult_thread=thread,
        verdict=parsed.verdict,
        consultant_model=model,
        consultant_effort=consultant_effort,
        consultant_substrate=substrate,
    )


def format_consult_provenance_md(prov: ConsultProvenance, *, evidence: str | None = None) -> str:
    """CHECKPOINT markdown block for the shared consult provenance schema."""
    effort_display = (
        prov.consultant_effort
        if prov.consultant_effort is not None
        else _EFFORT_UNMEASURED_TOKEN
    )
    lines = [
        "## Consult provenance",
        f"- consult_thread: {prov.consult_thread}",
        f"- verdict: {prov.verdict}",
        f"- consultant_model: {prov.consultant_model}",
        f"- consultant_effort: {effort_display}",
        f"- consultant_substrate: {prov.consultant_substrate}",
    ]
    if evidence:
        lines.append(f"- evidence: {evidence}")
    return "\n".join(lines) + "\n"
