"""Shared attributes input coercion for entity and assertion write models."""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError


def coerce_attributes_input(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "attributes must be a dict or JSON object string; got invalid JSON"
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"attributes JSON must decode to an object/dict, got {type(parsed).__name__}"
            )
        return parsed
    raise ValueError(
        f"attributes must be a dict or JSON object string, got {type(value).__name__}"
    )


def entity_payload_validation_exception(exc: ValidationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error": "entity_payload_invalid",
            "diagnostics": exc.errors(
                include_url=False,
                include_context=False,
                include_input=False,
            ),
        },
    )
