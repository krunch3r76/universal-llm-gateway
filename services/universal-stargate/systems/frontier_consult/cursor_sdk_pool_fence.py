"""Unattended Other-Models pool fence at cursor-sdk generate prepare.

Called from ``prepare_cursor_sdk_generate`` before mint; admits explicit
Other-Models ``model=`` pins with an advisory exempted event, hard-422 on omit.
"""

from __future__ import annotations

from cursor_capabilities import is_other_models_pool

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
    """Fence unattended ``op=generate`` / ``seat=cursor-sdk`` off the Other Models pool.

    Admission predicate (FOL):
      admit(model, resolved) ⟺ ¬other_models(resolved) ∨ explicit_pin(model)

    ``model`` is the raw request pin; it is ``None`` on the omit path, where
    ``resolved_model`` came from ``workflows.auto_judgment.model``. An explicit
    pin naming
    an Other Models id is a deliberate, sparing operator choice (``assertion:31945``)
    and is admitted with an advisory ``frontier.sdk.pool.exempted`` event. The omit
    path may never drift onto the capped pool through a profile default: hard 422
    with ``frontier.sdk.pool.denied``.
    """
    if not is_other_models_pool(resolved_model):
        return
    seat_label = (seat or role or "").strip() or "cursor-sdk"
    explicit_pin = (model or "").strip()
    if explicit_pin:
        emit_sdk_pool_exempted(
            request_id=request_id,
            seat=seat_label,
            requested_model=explicit_pin,
            resolved_model=resolved_model,
        )
        return
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
            "unattended op=generate on seat=cursor-sdk resolved to the Other Models "
            f"pool ({resolved_model}) without an explicit model= pin; the omit path "
            "stays on Cursor Models — pin the id explicitly (sparing use) or omit "
            "model= for the profile default"
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
