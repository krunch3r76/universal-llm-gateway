"""Batch required-field validation for cortex dispatch write ops.

Dispatch handlers historically returned on the first missing required field,
forcing codeblind seats through N round trips for N omissions. This module
collects all missing/invalid enum fields before the handler proceeds.
"""

from __future__ import annotations

from typing import Any

from fastapi import status

HTTP_422 = status.HTTP_422_UNPROCESSABLE_CONTENT


def is_missing(value: object) -> bool:
    """True when *value* is absent or blank after strip."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def field_error(
    field: str,
    message: str,
    *,
    accepted: list[str] | tuple[str, ...] | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"field": field, "message": message}
    if accepted is not None:
        entry["accepted"] = accepted
    if code is not None:
        entry["code"] = code
    return entry


def validation_error_response(errors: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a dispatch-surface 422 dict for one or more field errors."""
    if len(errors) == 1:
        lone = errors[0]
        detail: dict[str, Any] = {
            "error": lone.get("code", lone["message"]),
            "field": lone["field"],
            "message": lone["message"],
        }
        if "accepted" in lone:
            detail["accepted"] = lone["accepted"]
        return {"error": detail, "status_code": HTTP_422}
    return {
        "error": {
            "error": "missing_required_fields",
            "errors": [_public_error_entry(entry) for entry in errors],
        },
        "status_code": HTTP_422,
    }


def _public_error_entry(entry: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {
        "field": entry["field"],
        "message": entry["message"],
    }
    if "code" in entry:
        public["error"] = entry["code"]
    if "accepted" in entry:
        public["accepted"] = entry["accepted"]
    return public


def resolve_mutually_exclusive_aliases(
    *,
    primary: object,
    alias: object,
    primary_name: str,
    alias_name: str,
    conflict_code: str = "conflicting_alias_values",
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve two names for the same slot; error when both differ."""
    primary_text = None if is_missing(primary) else str(primary).strip()
    alias_text = None if is_missing(alias) else str(alias).strip()
    if primary_text and alias_text and primary_text != alias_text:
        return None, field_error(
            primary_name,
            (
                f"Supply {primary_name}= or {alias_name}= (same slot), not both "
                f"with different values."
            ),
            code=conflict_code,
        )
    return primary_text or alias_text, None


def collect_missing_required(
    fields: dict[str, object],
    *,
    message_for: dict[str, str] | None = None,
    enum_accepted: dict[str, list[str] | tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    """Collect per-field errors for every missing entry in *fields*."""
    msgs = message_for or {}
    enums = enum_accepted or {}
    errors: list[dict[str, Any]] = []
    for name, value in fields.items():
        if not is_missing(value):
            continue
        errors.append(
            field_error(
                name,
                msgs.get(name, f"{name} is required"),
                accepted=enums.get(name),
            )
        )
    return errors


def apply_entity_create_param_aliases(
    *,
    id: str | None,
    type: str | None,
    name: str | None,
    extra: dict[str, object],
) -> tuple[str | None, str | None, str | None]:
    """Map near-miss descriptor aliases when canonical params are absent."""
    if is_missing(id) and not is_missing(extra.get("entity_id")):
        id = str(extra["entity_id"])
    if is_missing(type) and not is_missing(extra.get("entity_type")):
        type = str(extra["entity_type"])
    if is_missing(name) and not is_missing(extra.get("title")):
        name = str(extra["title"])
    return id, type, name


def entity_create_preflight_errors(
    *,
    id: str | None,
    type: str | None,
    name: str | None,
    attributes: dict[str, Any] | str | None,
) -> list[dict[str, Any]]:
    """Top-level + todo density_triage checks before entity_create impl."""
    errors = collect_missing_required({"id": id, "type": type, "name": name})
    if type == "todo":
        from implement_admission.density_triage_create_gate import (
            format_implement_triage_unknown_reason,
        )
        from implement_admission.density_triage_gate import IMPLEMENT_GATE_TRIAGE

        from ..attributes_coerce import coerce_attributes_input

        attrs_shape_invalid = False
        attrs: dict[str, Any] = {}
        if attributes is not None:
            try:
                coerced = coerce_attributes_input(attributes)
                if coerced is not None:
                    attrs = coerced
            except ValueError:
                attrs_shape_invalid = True
        if not attrs_shape_invalid:
            raw = attrs.get("density_triage")
            triage = str(raw).strip() if raw is not None else ""
            if triage not in IMPLEMENT_GATE_TRIAGE:
                errors.append(
                    field_error(
                        "attributes.density_triage",
                        format_implement_triage_unknown_reason(
                            id or "todo:?", triage or None
                        ),
                        accepted=sorted(IMPLEMENT_GATE_TRIAGE),
                        code="density_triage_required",
                    )
                )
    return errors


__all__ = [
    "HTTP_422",
    "apply_entity_create_param_aliases",
    "collect_missing_required",
    "entity_create_preflight_errors",
    "field_error",
    "is_missing",
    "resolve_mutually_exclusive_aliases",
    "validation_error_response",
]
