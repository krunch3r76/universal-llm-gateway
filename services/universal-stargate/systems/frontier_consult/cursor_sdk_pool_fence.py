"""Other-Models pool fence at cursor-sdk generate prepare.

Called from ``prepare_cursor_sdk_generate`` before mint. Policy
``cursor_sdk_other_models_pool`` (route_policy.yaml): ``deny`` (default) rejects
omit and explicit pins; ``explicit_pin_only`` admits explicit pins with
``frontier.sdk.pool.exempted``.
"""

from __future__ import annotations

from implement_admission.cursor_sdk_other_models_pool import (
    load_cursor_sdk_other_models_pool_mode,
    other_models_pool_generate_admitted,
)

from .admission import FrontierEndpointError
from .cursor_sdk_generate_signals import emit_sdk_pool_denied, emit_sdk_pool_exempted

__all__ = ["reject_other_models_pool_generate"]


def reject_other_models_pool_generate(
    *,
    request_id: str,
    role: str,
    seat: str | None,
    model: str | None,
    resolved_model: str,
) -> None:
    """Fence cursor-sdk generate off the Other Models pool per route policy."""
    if other_models_pool_generate_admitted(
        model_pin=model, resolved_model=resolved_model
    ):
        seat_label = (seat or role or "").strip() or "cursor-sdk"
        explicit_pin = (model or "").strip()
        pool_mode = load_cursor_sdk_other_models_pool_mode()
        if explicit_pin and pool_mode == "explicit_pin_only":
            emit_sdk_pool_exempted(
                request_id=request_id,
                seat=seat_label,
                requested_model=explicit_pin,
                resolved_model=resolved_model,
            )
        return
    seat_label = (seat or role or "").strip() or "cursor-sdk"
    explicit_pin = (model or "").strip()
    emit_sdk_pool_denied(
        request_id=request_id,
        seat=seat_label,
        requested_model=model,
        resolved_model=resolved_model,
    )
    if explicit_pin:
        reason = (
            "cursor-sdk Other Models pool is retired "
            "(cursor_sdk_other_models_pool=deny); "
            f"explicit pin {resolved_model!r} is refused"
        )
    else:
        reason = (
            "unattended op=generate on seat=cursor-sdk resolved to the Other Models "
            f"pool ({resolved_model}) without an explicit model= pin; the omit path "
            "stays on Cursor Models — pin the id explicitly (sparing use) or omit "
            "model= for the profile default"
        )
    raise FrontierEndpointError(
        request_id=request_id,
        field="model",
        reason=reason,
        status_code=422,
        code="other_models_pool_denied",
        details={
            "requested_model": model,
            "resolved_model": resolved_model,
            "pool": "other_models",
            "retryable": False,
        },
    )
