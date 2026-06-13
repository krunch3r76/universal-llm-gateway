"""Cross-seat self-assessment rubric for delivery-audit token-locality campaigns."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .delivery_audit_baseline_reports import fetch_workflow_summaries
from .delivery_audit_baseline_types import SUMMARY_TOKEN_FIELDS

_LOCALITY_MET = 0.80
_LOCALITY_PARTIAL = 0.50
_DUP_MET = 0.10
_DUP_PARTIAL = 0.25
_RESTATE_MET = 0.10
_RESTATE_PARTIAL = 0.25
_TAG_FIELDS = ("campaign_id", "phase", "seat_substrate", "workflow_class")
_DEDUP_SCOPE_NOTE = "intra-trace only; session-scope deferred"

_DB_SCORED_DIMENSIONS = (
    "guidance_locality",
    "duplicate_guidance",
    "restatement_discipline",
    "delivery_auditability",
)
_JUDGMENT_DIMENSIONS = ("guidance_sufficiency", "missed_guidance")


def score_selfassess_rubric(
    campaign_id: str,
    *,
    phase: str = "baseline",
    seat_substrate: str | None = None,
    workflow_class: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Score acceptance §6 rubric dimensions from guidance workflow-summary rows."""
    rows = fetch_workflow_summaries(
        campaign_id=campaign_id,
        phase=phase,
        workflow_class=workflow_class,
        seat_substrate=seat_substrate,
        db_path=db_path,
    )
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row["workflow_class"], row["seat_substrate"])
        groups.setdefault(key, []).append(row)

    rubric_groups = [
        _score_group(workflow, seat, group_rows)
        for (workflow, seat), group_rows in sorted(groups.items())
    ]
    return {
        "campaign_id": campaign_id,
        "phase": phase,
        "workflow_class": workflow_class,
        "seat_substrate": seat_substrate,
        "rubric_groups": rubric_groups,
        "group_count": len(rubric_groups),
        "trace_count": len(rows),
    }


def _sum_token_vector(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        field: sum(int(row[field]) for row in rows) for field in SUMMARY_TOKEN_FIELDS
    }


def _score_group(
    workflow_class: str,
    seat_substrate: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    section_bytes = sum(int(row["section_bytes"]) for row in rows)
    whole_doc_bytes = sum(int(row["whole_doc_bytes"]) for row in rows)
    fetched_tokens = sum(int(row["fetched_guidance_tokens"]) for row in rows)
    duplicate_tokens = sum(int(row["duplicate_guidance_tokens"]) for row in rows)
    restated_tokens = sum(int(row["transcript_restated_tokens"]) for row in rows)

    return {
        "workflow_class": workflow_class,
        "seat_substrate": seat_substrate,
        "row_count": len(rows),
        "token_expense": _sum_token_vector(rows),
        "dimensions": {
            "guidance_locality": _score_guidance_locality(
                section_bytes,
                whole_doc_bytes,
            ),
            "duplicate_guidance": _score_duplicate_guidance(
                duplicate_tokens,
                fetched_tokens,
            ),
            "restatement_discipline": _score_restatement_discipline(
                restated_tokens,
                fetched_tokens,
            ),
            "delivery_auditability": _score_delivery_auditability(rows),
            "guidance_sufficiency": _score_guidance_sufficiency(rows),
            "missed_guidance": _score_missed_guidance(),
        },
    }


def _score_guidance_locality(
    section_bytes: int, whole_doc_bytes: int
) -> dict[str, Any]:
    denominator = section_bytes + whole_doc_bytes
    if denominator == 0:
        return {
            "verdict": "met",
            "evidence": {
                "local_ratio": None,
                "section_bytes": section_bytes,
                "whole_doc_bytes": whole_doc_bytes,
                "vacuous": True,
                "note": "zero scope bytes; locality vacuously met",
            },
        }
    ratio = section_bytes / denominator
    return {
        "verdict": _higher_is_better_verdict(
            ratio,
            _LOCALITY_MET,
            _LOCALITY_PARTIAL,
        ),
        "evidence": {
            "local_ratio": ratio,
            "section_bytes": section_bytes,
            "whole_doc_bytes": whole_doc_bytes,
        },
    }


def _score_duplicate_guidance(
    duplicate_tokens: int,
    fetched_tokens: int,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "duplicate_guidance_tokens": duplicate_tokens,
        "fetched_guidance_tokens": fetched_tokens,
        "dedup_scope_note": _DEDUP_SCOPE_NOTE,
    }
    if fetched_tokens == 0:
        return {
            "verdict": "met",
            "evidence": {
                **evidence,
                "dup_ratio": None,
                "vacuous": True,
                "note": "zero fetched guidance; duplicate ratio vacuously met",
            },
        }
    ratio = duplicate_tokens / fetched_tokens
    return {
        "verdict": _lower_is_better_verdict(ratio, _DUP_MET, _DUP_PARTIAL),
        "evidence": {**evidence, "dup_ratio": ratio},
    }


def _score_restatement_discipline(
    restated_tokens: int,
    fetched_tokens: int,
) -> dict[str, Any]:
    if restated_tokens == 0:
        evidence: dict[str, Any] = {
            "transcript_restated_tokens": restated_tokens,
            "fetched_guidance_tokens": fetched_tokens,
            "detector_unlanded": True,
            "note": "restatement detector not yet landed; zero restated tokens",
        }
        if fetched_tokens == 0:
            evidence["restate_ratio"] = None
            evidence["vacuous"] = True
        else:
            evidence["restate_ratio"] = 0.0
        return {"verdict": "met", "evidence": evidence}

    if fetched_tokens == 0:
        return {
            "verdict": "met",
            "evidence": {
                "transcript_restated_tokens": restated_tokens,
                "fetched_guidance_tokens": fetched_tokens,
                "restate_ratio": None,
                "vacuous": True,
                "note": "zero fetched guidance; restatement ratio vacuously met",
            },
        }
    ratio = restated_tokens / fetched_tokens
    return {
        "verdict": _lower_is_better_verdict(ratio, _RESTATE_MET, _RESTATE_PARTIAL),
        "evidence": {
            "transcript_restated_tokens": restated_tokens,
            "fetched_guidance_tokens": fetched_tokens,
            "restate_ratio": ratio,
            "detector_unlanded": False,
        },
    }


def _score_delivery_auditability(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "verdict": "unmet",
            "evidence": {"row_count": 0, "note": "no summary rows in slice"},
        }

    untagged_rows = [
        row["workflow_summary_id"]
        for row in rows
        if any(not row.get(field) for field in _TAG_FIELDS)
    ]
    zero_artifact_rows = [
        row["workflow_summary_id"] for row in rows if int(row["artifact_count"]) < 1
    ]
    evidence = {
        "row_count": len(rows),
        "untagged_row_ids": untagged_rows,
        "zero_artifact_row_ids": zero_artifact_rows,
    }
    if untagged_rows or zero_artifact_rows:
        return {"verdict": "partial", "evidence": evidence}
    return {"verdict": "met", "evidence": evidence}


def _score_guidance_sufficiency(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resident = sum(int(row["resident_guidance_tokens"]) for row in rows)
    fetched = sum(int(row["fetched_guidance_tokens"]) for row in rows)
    artifact_count = sum(int(row["artifact_count"]) for row in rows)
    trigger_fan_in_max = max(
        (int(row["trigger_fan_in_count"]) for row in rows),
        default=0,
    )
    return {
        "verdict": "agent_judgment_required",
        "evidence": {
            "artifact_count": artifact_count,
            "resident_guidance_tokens": resident,
            "fetched_guidance_tokens": fetched,
            "trigger_fan_in_count_max": trigger_fan_in_max,
        },
    }


def _score_missed_guidance() -> dict[str, Any]:
    return {
        "verdict": "agent_judgment_required",
        "evidence": {"db_signal": None},
    }


def _higher_is_better_verdict(
    ratio: float,
    met_threshold: float,
    partial_threshold: float,
) -> str:
    if ratio >= met_threshold:
        return "met"
    if ratio >= partial_threshold:
        return "partial"
    return "unmet"


def _lower_is_better_verdict(
    ratio: float,
    met_threshold: float,
    partial_threshold: float,
) -> str:
    if ratio <= met_threshold:
        return "met"
    if ratio <= partial_threshold:
        return "partial"
    return "unmet"


def render_selfassess_closeout(report: dict[str, Any]) -> str:
    """Render a deterministic markdown closeout block from a rubric report."""
    lines = [
        "# Token Locality Self-Assessment",
        "",
        f"Campaign: `{report.get('campaign_id', '')}` | Phase: `{report.get('phase', '')}`",
        f"Groups: {report.get('group_count', 0)} | Traces: {report.get('trace_count', 0)}",
        "",
    ]
    for group in report.get("rubric_groups", []):
        lines.extend(_render_group_block(group))
    return "\n".join(lines).rstrip() + "\n"


def _render_group_block(group: dict[str, Any]) -> list[str]:
    workflow = group.get("workflow_class", "")
    seat = group.get("seat_substrate", "")
    lines = [
        f"## {workflow} × {seat}",
        "",
        "| Dimension | Verdict | Evidence |",
        "| --- | --- | --- |",
    ]
    dimensions = group.get("dimensions", {})
    for name in _DB_SCORED_DIMENSIONS + _JUDGMENT_DIMENSIONS:
        dim = dimensions.get(name, {})
        verdict = dim.get("verdict", "")
        evidence = dim.get("evidence", {})
        lines.append(f"| {name} | {verdict} | {_format_evidence(evidence)} |")

    token_expense = group.get("token_expense", {})
    lines.extend(
        [
            "",
            "**Token expense (summed):**",
            "",
        ]
    )
    for field in SUMMARY_TOKEN_FIELDS:
        lines.append(f"- `{field}`: {token_expense.get(field, 0)}")
    lines.append("")
    return lines


def _format_evidence(evidence: dict[str, Any]) -> str:
    if not evidence:
        return ""
    parts = [f"{key}={value!r}" for key, value in sorted(evidence.items())]
    return "; ".join(parts)
