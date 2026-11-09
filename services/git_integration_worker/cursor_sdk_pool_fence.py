"""Other Models pool fence at GIW POST /dispatch admit (nested cursor-sdk)."""

from __future__ import annotations

from cursor_capabilities import canonical_cursor_bare_id
from implement_admission.cursor_sdk_other_models_pool import (
    other_models_pool_generate_admitted,
)

from services.git_integration_worker.models.cursor_api import CursorDispatchRequest


def other_models_pool_dispatch_rejected(
    req: CursorDispatchRequest,
    *,
    resolved_bare_model: str,
) -> bool:
    """True when policy refuses this dispatch's resolved Other Models model."""
    pin = str(req.model or "").strip() or None
    resolved_wire = f"cursor/{canonical_cursor_bare_id(resolved_bare_model)}"
    return not other_models_pool_generate_admitted(
        model_pin=pin,
        resolved_model=resolved_wire,
    )


__all__ = ["other_models_pool_dispatch_rejected"]
