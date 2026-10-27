"""Closed JSON schemas for semantic_judge pin and quality model calls."""

from __future__ import annotations

from typing import Any

SPEECH_ACTS = ("request", "assertion", "other")

_PIN_ROW_KEYS = frozenset(
    {"pin_id", "satisfied", "polarity_match", "evidence_span", "candidate_id"}
)
_CLAUSE_ROW_KEYS = frozenset(
    {"text", "pin_ids", "speech_act", "attested", "candidate_id"}
)
_QUALITY_ROW_KEYS = frozenset({"rule", "detail", "penalty", "candidate_id"})

_PIN_ROW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pin_id": {"type": "string"},
        "satisfied": {"type": "boolean"},
        "polarity_match": {"type": "boolean"},
        "evidence_span": {"type": "string"},
    },
    "required": ["pin_id", "satisfied", "polarity_match", "evidence_span"],
}

_CLAUSE_ROW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text": {"type": "string"},
        "pin_ids": {"type": "array", "items": {"type": "string"}},
        "speech_act": {"type": "string", "enum": list(SPEECH_ACTS)},
        "attested": {"type": "boolean"},
    },
    "required": ["text", "pin_ids", "speech_act", "attested"],
}

PIN_CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "candidate_id": {"type": "string", "minLength": 1},
                    "pins": {"type": "array", "items": _PIN_ROW_SCHEMA},
                    "clauses": {"type": "array", "items": _CLAUSE_ROW_SCHEMA},
                },
                "required": ["candidate_id", "pins", "clauses"],
            },
        },
    },
    "required": ["candidates"],
}

PIN_SINGLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "pins": {"type": "array", "items": _PIN_ROW_SCHEMA},
        "clauses": {"type": "array", "items": _CLAUSE_ROW_SCHEMA},
    },
    "required": ["pins", "clauses"],
}

_QUALITY_FINDING_CANDIDATES_ROW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidate_id": {"type": "string", "minLength": 1},
        "rule": {"type": "string"},
        "detail": {"type": "string"},
        "penalty": {"type": "integer"},
    },
    "required": ["candidate_id", "rule", "detail", "penalty"],
}

_QUALITY_FINDING_SINGLE_ROW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "rule": {"type": "string"},
        "detail": {"type": "string"},
        "penalty": {"type": "integer"},
    },
    "required": ["rule", "detail", "penalty"],
}

QUALITY_CANDIDATES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": _QUALITY_FINDING_CANDIDATES_ROW,
        },
    },
    "required": ["findings"],
}

QUALITY_SINGLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": _QUALITY_FINDING_SINGLE_ROW,
        },
    },
    "required": ["findings"],
}


def _pin_row_ok(row: Any, *, require_candidate: bool) -> str | None:
    if not isinstance(row, dict):
        return "pin_not_object"
    if set(row) - _PIN_ROW_KEYS:
        return "pin_extra_keys"
    for key in ("pin_id", "evidence_span"):
        if not isinstance(row.get(key), str):
            return "pin_fields"
    for key in ("satisfied", "polarity_match"):
        if not isinstance(row.get(key), bool):
            return "pin_fields"
    if require_candidate and not isinstance(row.get("candidate_id"), str):
        return "pin_candidate_id"
    return None


def _clause_row_ok(row: Any, *, require_candidate: bool) -> str | None:
    if not isinstance(row, dict):
        return "clause_not_object"
    if set(row) - _CLAUSE_ROW_KEYS:
        return "clause_extra_keys"
    if not isinstance(row.get("text"), str):
        return "clause_fields"
    pin_ids = row.get("pin_ids")
    if not isinstance(pin_ids, list) or not all(isinstance(p, str) for p in pin_ids):
        return "clause_fields"
    if row.get("speech_act") not in SPEECH_ACTS:
        return "clause_speech_act"
    if not isinstance(row.get("attested"), bool):
        return "clause_fields"
    if require_candidate and not isinstance(row.get("candidate_id"), str):
        return "clause_candidate_id"
    return None


def pin_judgment_error(obj: Any, *, multi_candidate: bool) -> str | None:
    """Return a short reason when ``obj`` fails pin-judgment validation."""
    if not isinstance(obj, dict):
        return "not_object"
    extra = set(obj) - {"candidates", "pins", "clauses"}
    if extra:
        return "additional_properties"
    if multi_candidate:
        candidates = obj.get("candidates")
        if not isinstance(candidates, list):
            return "candidates"
        for block in candidates:
            if not isinstance(block, dict):
                return "candidate_not_object"
            if set(block) - {"candidate_id", "pins", "clauses"}:
                return "candidate_extra_keys"
            cid = block.get("candidate_id")
            if not isinstance(cid, str) or not cid.strip():
                return "candidate_id"
            pins = block.get("pins")
            clauses = block.get("clauses")
            if not isinstance(pins, list) or not isinstance(clauses, list):
                return "candidate_arrays"
            for row in pins:
                err = _pin_row_ok(row, require_candidate=False)
                if err:
                    return err
            for row in clauses:
                err = _clause_row_ok(row, require_candidate=False)
                if err:
                    return err
        return None
    pins = obj.get("pins")
    clauses = obj.get("clauses")
    if not isinstance(pins, list) or not isinstance(clauses, list):
        return "v2_arrays"
    if obj.get("candidates") is not None:
        return "v2_has_candidates"
    for row in pins:
        if _pin_row_ok(row, require_candidate=False):
            return "pin_fields"
    for row in clauses:
        if _clause_row_ok(row, require_candidate=False):
            return "clause_fields"
    return None


def _quality_row_ok(row: Any, *, require_candidate: bool) -> str | None:
    if not isinstance(row, dict):
        return "finding_not_object"
    if set(row) - _QUALITY_ROW_KEYS:
        return "finding_extra_keys"
    if require_candidate and not isinstance(row.get("candidate_id"), str):
        return "finding_candidate_id"
    cid = row.get("candidate_id")
    if cid is not None and not isinstance(cid, str):
        return "finding_candidate_id"
    if not isinstance(row.get("rule"), str) or not isinstance(row.get("detail"), str):
        return "finding_fields"
    if not isinstance(row.get("penalty"), int):
        return "finding_penalty"
    return None


def quality_judgment_error(obj: Any, *, multi_candidate: bool) -> str | None:
    if not isinstance(obj, dict):
        return "not_object"
    if set(obj) - {"findings"}:
        return "additional_properties"
    findings = obj.get("findings")
    if not isinstance(findings, list):
        return "findings"
    for row in findings:
        err = _quality_row_ok(row, require_candidate=multi_candidate)
        if err:
            return err
    return None


def flatten_pin_judgment(
    obj: dict[str, Any], *, multi_candidate: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return flat pin rows and clause rows with optional candidate_id."""
    pins: list[dict[str, Any]] = []
    clauses: list[dict[str, Any]] = []
    if multi_candidate:
        for block in obj.get("candidates") or []:
            if not isinstance(block, dict):
                continue
            cid = str(block.get("candidate_id") or "")
            for row in block.get("pins") or []:
                if isinstance(row, dict):
                    pins.append({**row, "candidate_id": cid})
            for row in block.get("clauses") or []:
                if isinstance(row, dict):
                    clauses.append({**row, "candidate_id": cid})
        return pins, clauses
    for row in obj.get("pins") or []:
        if isinstance(row, dict):
            pins.append(dict(row))
    for row in obj.get("clauses") or []:
        if isinstance(row, dict):
            clauses.append(dict(row))
    return pins, clauses


def flatten_quality_judgment(
    obj: dict[str, Any], *, multi_candidate: bool
) -> list[dict[str, Any]]:
    _ = multi_candidate
    return [dict(row) for row in obj.get("findings") or [] if isinstance(row, dict)]
