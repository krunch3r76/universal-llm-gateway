"""Status claim×measure polarity entitlement — P1 incomplete-class split (arc 6655).

Splits polysemous ``partial`` into ``partial:work`` vs ``partial:capture`` so
claim×measure cells can settle. Routes ``complete×partial`` to ``plane-legend:``
(not uniform ``plane-discrepancy:``). Authority table is code, not prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from implement_admission.spec import NO_RUN_DEGRADED_REASONS, CloseoutStatus, WorkOutcome

StatusIncompleteClass = Literal["work", "capture"]
StatusAuthorityWinner = Literal["measure", "claim"]
NextStepAuthority = Literal[
    "deviations_qualified_measure",
    "bare_claim",
    "bare_measure",
]

_PLANE_LEGEND_RE = re.compile(r"(?im)^plane-legend:\s*.+$")
_INCOMPLETE_CLASS_SUFFIX_RE = re.compile(r"^(partial):(work|capture)$", re.I)
_MEASURE_ALIAS_MAP = {
    "failed": "blocked",
    "gated": "blocked",
    "shipped": "complete",
}

_WORK_DEVIATION_MARKERS = frozenset({"land:lane_b_unlanded"})
_CAPTURE_DEVIATION_PREFIXES = ("capture:", "divergence:", "degraded:sdk_git")


def normalize_measurement_token(measurement: str) -> str:
    """Strip incomplete-class suffix; map shipped/failed/gated aliases."""
    text = (measurement or "").strip().lower()
    if text == "relay_parse_failed":
        return text
    match = _INCOMPLETE_CLASS_SUFFIX_RE.match(text)
    base = match.group(1) if match else text
    return _MEASURE_ALIAS_MAP.get(base, base)


def measurement_incomplete_class(measurement: str) -> StatusIncompleteClass | None:
    """Return work/capture suffix when *measurement* carries ``partial:*``."""
    match = _INCOMPLETE_CLASS_SUFFIX_RE.match((measurement or "").strip().lower())
    if match is None:
        return None
    return match.group(2)  # type: ignore[return-value]


def qualify_partial_measurement(
    status: str,
    incomplete_class: StatusIncompleteClass | None,
) -> str:
    """Stamp reason class beside bare ``partial`` for envelope / annotate display."""
    normalized = (status or "").strip().lower()
    if normalized != "partial" or incomplete_class is None:
        return normalized
    return f"partial:{incomplete_class}"


def classify_status_incomplete_class(
    *,
    status: CloseoutStatus,
    work_outcome: WorkOutcome | None,
    capture_status: str | None,
    escalation_harvest: str | None,
    deviations: list[str] | None,
    degraded_reason: str | None = None,
) -> StatusIncompleteClass | None:
    """Classify why ``status`` is partial — work residual vs capture/measure noise."""
    if status != CloseoutStatus.PARTIAL:
        return None
    devs = list(deviations or [])
    if work_outcome == WorkOutcome.CHECKS_FAILED:
        return "work"
    if work_outcome == WorkOutcome.NOT_SHIPPED:
        return "work"
    if any(marker in devs for marker in _WORK_DEVIATION_MARKERS):
        return "work"
    if (escalation_harvest or "none") == "open":
        return "work"
    if degraded_reason and degraded_reason.startswith("run_status="):
        return "work"
    if degraded_reason in NO_RUN_DEGRADED_REASONS:
        return "work"
    if capture_status in ("partial", "unavailable"):
        return "capture"
    if any(
        d.startswith(prefix)
        for d in devs
        for prefix in _CAPTURE_DEVIATION_PREFIXES
    ):
        return "capture"
    if work_outcome == WorkOutcome.UNVERIFIED:
        return "capture"
    return "capture"


def incomplete_class_from_wrapper(payload: dict[str, object]) -> StatusIncompleteClass | None:
    """Read stamped class from ImplementCloseout JSON when present."""
    raw = payload.get("status_incomplete_class")
    if raw in ("work", "capture"):
        return raw  # type: ignore[return-value]
    return None


def resolve_qualified_measurement_status(
    *,
    base_status: str,
    wrapper_text: str | None,
    incomplete_class: StatusIncompleteClass | None = None,
) -> str:
    """Return measurement token with ``partial:work|capture`` when applicable."""
    normalized = (base_status or "").strip().lower()
    if normalized != "partial":
        return normalized
    if incomplete_class is None and wrapper_text:
        try:
            import json

            payload = json.loads(wrapper_text)
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            incomplete_class = incomplete_class_from_wrapper(payload)
    return qualify_partial_measurement(normalized, incomplete_class)


def status_claim_is_dual_register_honesty(*, claim: str, measurement: str) -> bool:
    """True for partial-claim vs machine-complete honesty (plane-register)."""
    claim_norm = normalize_measurement_token(claim)
    measure_norm = normalize_measurement_token(measurement)
    return claim_norm == "partial" and measure_norm == "complete"


def status_claim_is_polysemous_partial_legend(*, claim: str, measurement: str) -> bool:
    """True for complete-claim vs polysemous partial measure (plane-legend)."""
    claim_norm = normalize_measurement_token(claim)
    measure_norm = normalize_measurement_token(measurement)
    return claim_norm == "complete" and measure_norm == "partial"


def status_dispositions_equivalent(claim: str, measurement: str) -> bool:
    """True when agent claim and infra measurement describe the same closeout status."""
    claim_norm = normalize_measurement_token(claim)
    measure_norm = normalize_measurement_token(measurement)
    if measure_norm == "relay_parse_failed":
        return False
    return claim_norm == measure_norm


def annotate_status_claim_discrepancy(
    *,
    claim: str | None,
    measurement: str,
) -> str | None:
    """Emit annotate-only marker when §2 claim diverges from infra ``status:``."""
    if claim is None or not claim.strip():
        return None
    if status_dispositions_equivalent(claim, measurement):
        return None
    claim_display = claim.strip().lower()
    measure_display = (measurement or "").strip().lower()
    return (
        f"status_claim@§2 {claim_display} "
        f"while status@infra {measure_display}"
    )


@dataclass(frozen=True, slots=True)
class StatusDisagreementAuthority:
    """Which register wins per downstream question when claim ≠ measure."""

    work_outcome: StatusAuthorityWinner
    ac_pass: StatusAuthorityWinner
    next_step: NextStepAuthority


def _deviations_qualify_next_step(
    deviations: list[str] | None,
    incomplete_class: StatusIncompleteClass | None,
) -> bool:
    devs = list(deviations or [])
    if incomplete_class == "work":
        return True
    if any(marker in devs for marker in _WORK_DEVIATION_MARKERS):
        return True
    if any(
        d.startswith(prefix)
        for d in devs
        for prefix in _CAPTURE_DEVIATION_PREFIXES
    ):
        return True
    return False


def resolve_status_disagreement_authority(
    *,
    claim: str | None,
    measurement: str,
    work_outcome: str | None = None,
    capture_status: str | None = None,
    deviations: list[str] | None = None,
) -> StatusDisagreementAuthority | None:
    """Authority table for claim≠measure; ``None`` when absent or equivalent."""
    if claim is None or not claim.strip():
        return None
    if status_dispositions_equivalent(claim, measurement):
        return None
    incomplete_class = measurement_incomplete_class(measurement)
    if _deviations_qualify_next_step(deviations, incomplete_class):
        next_step: NextStepAuthority = "deviations_qualified_measure"
    elif incomplete_class is None and normalize_measurement_token(measurement) == "partial":
        next_step = "bare_measure"
    else:
        next_step = "bare_claim"
    del work_outcome, capture_status  # reserved for future deviation-aware tie-break
    return StatusDisagreementAuthority(
        work_outcome="measure",
        ac_pass="measure",
        next_step=next_step,
    )


def merge_plane_legend_markers(*parts: str | None) -> str | None:
    """Join polysemous-partial fragments into one ``plane-legend:`` line."""
    markers: list[str] = []
    for part in parts:
        if not part:
            continue
        text = part.strip()
        if text.casefold().startswith("plane-legend:"):
            text = text.split(":", 1)[1].strip()
        if text:
            markers.append(text)
    if not markers:
        return None
    return "plane-legend: " + "; ".join(markers)


def merge_plane_discrepancy_markers(*parts: str | None) -> str | None:
    """Join discrepancy fragments; honesty + legend pairs stay out of defect register."""
    markers: list[str] = []
    for part in parts:
        if not part:
            continue
        text = part.strip()
        if text.casefold().startswith("plane-discrepancy:"):
            text = text.split(":", 1)[1].strip()
        if text.casefold().startswith("plane-register:"):
            continue
        if text.casefold().startswith("plane-legend:"):
            continue
        status_match = re.match(
            r"^status_claim@§2\s+(\S+)\s+while\s+status@infra\s+(\S+)$",
            text,
            re.I,
        )
        if status_match is not None:
            claim_tok = status_match.group(1)
            measure_tok = status_match.group(2)
            if status_claim_is_dual_register_honesty(
                claim=claim_tok,
                measurement=measure_tok,
            ):
                continue
            if status_claim_is_polysemous_partial_legend(
                claim=claim_tok,
                measurement=measure_tok,
            ):
                continue
        if text:
            markers.append(text)
    if not markers:
        return None
    return "plane-discrepancy: " + "; ".join(markers)


def inject_plane_legend_line(body: str, *, value: str | None) -> str:
    """Inject annotate-only ``plane-legend:`` marker; no-op when value is None."""
    if not value:
        return body
    line = value if value.startswith("plane-legend:") else f"plane-legend: {value}"
    if _PLANE_LEGEND_RE.search(body):
        return _PLANE_LEGEND_RE.sub(line, body, count=1)
    register_match = re.search(r"(?im)^plane-register:\s*.+$", body)
    if register_match:
        insert_at = register_match.end()
        return f"{body[:insert_at]}\n{line}{body[insert_at:]}"
    plane_match = re.search(r"(?im)^plane:\s*.+$", body)
    if plane_match:
        insert_at = plane_match.end()
        return f"{body[:insert_at]}\n{line}{body[insert_at:]}"
    return body.rstrip() + f"\n{line}\n"


__all__ = [
    "NextStepAuthority",
    "StatusAuthorityWinner",
    "StatusDisagreementAuthority",
    "StatusIncompleteClass",
    "annotate_status_claim_discrepancy",
    "classify_status_incomplete_class",
    "incomplete_class_from_wrapper",
    "inject_plane_legend_line",
    "measurement_incomplete_class",
    "merge_plane_discrepancy_markers",
    "merge_plane_legend_markers",
    "normalize_measurement_token",
    "qualify_partial_measurement",
    "resolve_qualified_measurement_status",
    "resolve_status_disagreement_authority",
    "status_claim_is_dual_register_honesty",
    "status_claim_is_polysemous_partial_legend",
    "status_dispositions_equivalent",
]
