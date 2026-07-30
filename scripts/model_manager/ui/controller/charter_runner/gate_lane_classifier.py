"""Classify charter window kind from machine-authored Steps lane annotations.

Lane tokens on Steps titles (machine-authored):
  [consult:r_admit] | [consult:judgment_gap] | [implement] | [inline] | [judgment]

The classifier owns ``window_kind`` when open Steps rows carry annotations; the
checkpoint parser extracts titles only and does not adjudicate lanes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .checkpoint_schema import Step

# Lane tokens on Steps titles (machine-authored):
#   [consult:r_admit] | [consult:judgment_gap] | [implement] | [inline] | [judgment]
LANE_ANNOTATION_RE = re.compile(
    r"\[(consult:(?:r_admit|judgment_gap)|implement|inline|judgment)\]",
    re.I,
)
_GATE_ID_RE = re.compile(r"\b(G\d+)\b", re.I)

_CONSULT_LANE_PREFIX = "consult:"


@dataclass(frozen=True)
class GateRow:
    ordinal: int
    status: str  # pending|in_progress|done|blocked
    title: str
    lane: str | None  # annotation or None


@dataclass(frozen=True)
class RequiredLane:
    kind: str  # consult | worker
    role: str | None  # r_admit | judgment_gap | None
    gate_id: str | None  # e.g. G3
    reason: str


def _extract_gate_id(title: str) -> str | None:
    match = _GATE_ID_RE.search(title)
    return match.group(1).upper() if match else None


def _normalize_lane(raw: str) -> str:
    return raw.lower()


def parse_gate_rows(steps: list[Step] | list) -> list[GateRow]:
    """Map parsed Steps into gate rows with optional lane annotations."""
    rows: list[GateRow] = []
    for step in steps:
        lane: str | None = None
        match = LANE_ANNOTATION_RE.search(step.title)
        if match:
            lane = _normalize_lane(match.group(1))
        rows.append(
            GateRow(
                ordinal=step.ordinal,
                status=step.status,
                title=step.title,
                lane=lane,
            )
        )
    return rows


def classify(rows: list[GateRow]) -> RequiredLane | None:
    """First open (not done) row with a lane annotation → RequiredLane.

    Returns None when no annotations on any open row (legacy path).
    """
    for row in rows:
        if row.status == "done":
            continue
        if row.lane is None:
            continue
        gate_id = _extract_gate_id(row.title)
        if row.lane.startswith(_CONSULT_LANE_PREFIX):
            role = row.lane.split(":", 1)[1]
            return RequiredLane(
                kind="consult",
                role=role,
                gate_id=gate_id,
                reason=f"steps_annotation:{row.lane}",
            )
        if row.lane in {"implement", "judgment", "inline"}:
            return RequiredLane(
                kind="worker",
                role=None,
                gate_id=gate_id,
                reason=f"steps_annotation:{row.lane}",
            )
    return None


def any_lane_annotations(rows: list[GateRow]) -> bool:
    """True when any gate row carries a lane annotation (open or done)."""
    return any(row.lane is not None for row in rows)


def open_lane_annotation_mismatch(
    rows: list[GateRow], req: RequiredLane | None
) -> bool:
    """True when an open annotated row exists but classify returned None."""
    if req is not None:
        return False
    return any(
        row.status != "done" and row.lane is not None for row in rows
    )


def resolve_admit_lane(
    parsed,
    *,
    default_admission_mode: str,
    root_id: str,
    log,
) -> tuple[str, str, str | None, object, str | None]:
    """Return ``(window_kind, admission_mode, consult_role, parsed, refuse_reason)``."""
    from dataclasses import replace

    rows = parse_gate_rows(parsed.steps)
    req = classify(rows)
    if open_lane_annotation_mismatch(rows, req):
        return "worker", default_admission_mode, None, parsed, "missing_lane_annotation"
    if req is not None and req.kind == "consult":
        if not parsed.consult_pending:
            log.warning(
                "classifier_consult_without_consult_pending root=%s role=%s",
                root_id,
                req.role,
            )
        parsed = replace(parsed, consult_role=req.role)
        return "consult", "consult", req.role, parsed, None
    if req is not None and req.kind == "worker" and parsed.consult_pending:
        log.warning(
            "classifier_worker_overrides_consult_pending root=%s gate=%s",
            root_id,
            req.gate_id,
        )
    if req is None and parsed.consult_pending and not any_lane_annotations(rows):
        log.warning(
            "legacy_consult_pending_no_lane_annotations root=%s",
            root_id,
        )
    if parsed.consult_pending:
        return (
            "consult",
            "consult",
            parsed.consult_role,
            parsed,
            None,
        )
    return "worker", default_admission_mode, None, parsed, None


__all__ = [
    "GateRow",
    "LANE_ANNOTATION_RE",
    "RequiredLane",
    "any_lane_annotations",
    "classify",
    "open_lane_annotation_mismatch",
    "parse_gate_rows",
    "resolve_admit_lane",
]
