"""Unattended Other-Models pool fence at cursor-sdk generate prepare."""

from __future__ import annotations

from cursor_capabilities import is_other_models_pool

from .admission import FrontierEndpointError
from .cursor_sdk_generate_signals import emit_sdk_pool_denied

__all__ = ["reject_other_models_pool_generate"]


def reject_other_models_pool_generate(
    *,
    request_id: str,
    role: str,
    seat: str | None,
    model: str | None,
    resolved_model: str,
) -> None:
    """Hard-422 Other Models on unattended ``op=generate`` / ``seat=cursor-sdk``."""
    if not is_other_models_pool(resolved_model):
        return
    seat_label = (seat or role or "").strip() or "cursor-sdk"
    emit_sdk_pool_denied(
        request_id=request_id,
        seat=seat_label,
        requested_model=model,
        resolved_model=resolved_model,
    )
    raise FrontierEndpointError(
        request_id=request_id,
        field="model",
        reason=(
            "unattended op=generate on seat=cursor-sdk cannot draw the Other Models "
            "pool; pin a Cursor Models id (grok / composer) or use op=handoff"
        ),
        status_code=422,
        code="other_models_pool_denied",
        details={
            "requested_model": model,
            "resolved_model": resolved_model,
            "pool": "other_models",
            "retryable": False,
        },
    )
