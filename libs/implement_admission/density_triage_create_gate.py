"""Create-time density_triage gate for todo entities.

Fail-closed at ``entity_create`` so implement dispatch never discovers unset
triage mid-admit (5867/5870 dogfood). Opus does not set this — cursor densifies
at mint. Vocabulary shared with implement-lane ``IMPLEMENT_GATE_TRIAGE``.
"""

from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException, status

from implement_admission.density_triage_gate import (
    IMPLEMENT_GATE_TRIAGE,
    format_implement_triage_unknown_reason,
)


def validate_todo_density_triage_at_create(
    entity_id: str,
    attributes: Mapping[str, Any] | None,
) -> None:
    """Raise HTTP 422 when todo lacks a valid implement-lane density_triage."""
    attrs = dict(attributes or {})
    raw = attrs.get("density_triage")
    triage = str(raw).strip() if raw is not None else ""
    if triage in IMPLEMENT_GATE_TRIAGE:
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "error": "density_triage_required",
            "field": "attributes.density_triage",
            "message": format_implement_triage_unknown_reason(
                entity_id, triage or None
            ),
            "accepted": sorted(IMPLEMENT_GATE_TRIAGE),
        },
    )


__all__ = ["validate_todo_density_triage_at_create"]
