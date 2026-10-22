"""JSON schema for a local reviewer result.

The same object is the ``json_schema`` on the local review model call and
the check the select step runs before revise binds findings. It matches the
``review_local`` generate step at commit 4c1deffd: object, no extra keys,
``findings`` of ``{ref, type, fix}``, ``verdict`` ``ship`` or ``revise``,
``clauses`` of ``{text, pin_ids, disposition}``.
"""

from __future__ import annotations

from typing import Any

FINDING_TYPES = (
    "unsupported",
    "contradicts_pin",
    "unattested_fact",
    "unraised_denial",
    "register",
    "sequitur",
    "omission",
    "not_purpose_relevant",
    "intake_answer_promoted",
    "volunteered_negative_or_concession",
    "prior_draft_carryover",
)

REVIEW_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "ref": {"type": "string"},
                    "type": {"type": "string", "enum": list(FINDING_TYPES)},
                    "fix": {"type": "string"},
                },
                "required": ["ref", "type", "fix"],
            },
        },
        "verdict": {"type": "string", "enum": ["ship", "revise"]},
        "clauses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "pin_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "disposition": {"type": "string"},
                },
                "required": ["text", "pin_ids", "disposition"],
            },
        },
    },
    "required": ["findings", "verdict", "clauses"],
}

_FINDING_KEYS = frozenset({"ref", "type", "fix"})
_CLAUSE_KEYS = frozenset({"text", "pin_ids", "disposition"})
_TYPES = frozenset(FINDING_TYPES)


def review_result_error(obj: Any) -> str | None:
    """Return a short reason when ``obj`` is not a review result, else None."""
    if not isinstance(obj, dict):
        return "not_object"
    extra = set(obj) - {"findings", "verdict", "clauses"}
    if extra:
        return "additional_properties"
    if "verdict" not in obj:
        return "missing_verdict"
    if obj.get("verdict") not in {"ship", "revise"}:
        return "bad_verdict"
    findings = obj.get("findings")
    if not isinstance(findings, list):
        return "findings"
    for row in findings:
        if not isinstance(row, dict):
            return "finding_not_object"
        if set(row) - _FINDING_KEYS:
            return "finding_additional_properties"
        if not isinstance(row.get("ref"), str) or not isinstance(row.get("fix"), str):
            return "finding_fields"
        if row.get("type") not in _TYPES:
            return "finding_type"
    if "clauses" not in obj:
        return "missing_clauses"
    clauses = obj.get("clauses")
    if not isinstance(clauses, list):
        return "clauses"
    for row in clauses:
        if not isinstance(row, dict):
            return "clause_not_object"
        if set(row) - _CLAUSE_KEYS:
            return "clause_additional_properties"
        if not isinstance(row.get("text"), str):
            return "clause_fields"
        pin_ids = row.get("pin_ids")
        if not isinstance(pin_ids, list):
            return "clause_fields"
        if not all(isinstance(pin_id, str) for pin_id in pin_ids):
            return "clause_fields"
        if not isinstance(row.get("disposition"), str):
            return "clause_fields"
    return None
