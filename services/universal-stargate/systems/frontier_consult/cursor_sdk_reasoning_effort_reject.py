"""BIND_B — hard-422 non-empty ``reasoning_effort`` on cursor-sdk prepare."""

from __future__ import annotations

from cursor_capabilities import (
    effort_knob_name,
    suggest_effort_knobs,
    supported_knobs,
)

from .admission import FrontierEndpointError
from .cursor_sdk_generate_signals import emit_sdk_reasoning_effort_rejected

__all__ = ["reject_nonempty_reasoning_effort"]


def _wire_model_id(resolved_model: str) -> str:
    if resolved_model.startswith("cursor/"):
        return resolved_model.removeprefix("cursor/")
    return resolved_model


def reject_nonempty_reasoning_effort(
    *,
    request_id: str,
    resolved_model: str,
    reasoning_effort: str | None,
) -> None:
    """Hard-422 non-empty ``reasoning_effort`` on cursor-sdk (BIND_B).

    ``None`` / ``\"\"`` are absent — admit. Remedy is descriptor-computed and
    never applied to ``aligned_knobs``.
    """
    requested = (reasoning_effort or "").strip()
    if not requested:
        return
    model_id = _wire_model_id(resolved_model)
    knob = effort_knob_name(model_id)
    suggested = suggest_effort_knobs(model_id, requested)
    supported: list[str] = []
    if knob is not None:
        spec = supported_knobs(model_id).get(knob)
        if spec is not None:
            supported = list(spec.accepted)
    details = {
        "use": "model_knobs",
        "knob": knob,
        "requested": requested,
        "suggested_model_knobs": suggested,
        "supported": supported,
        "model": model_id,
    }
    if suggested:
        remedy = f"pass model_knobs={suggested!r}"
    elif knob is None:
        remedy = (
            f"model {model_id!r} exposes no effort-like knob; omit reasoning_effort"
        )
    else:
        remedy = (
            f"pass model_knobs.{{{knob}}} with a value in {supported!r} "
            f"(requested {requested!r} is not accepted)"
        )
    emit_sdk_reasoning_effort_rejected(model_id=model_id, requested=requested)
    raise FrontierEndpointError(
        request_id=request_id,
        field="reasoning_effort",
        reason=(
            "reasoning_effort is not supported on seat=cursor-sdk; "
            f"use model_knobs instead ({remedy})"
        ),
        status_code=422,
        code="reasoning_effort_not_supported",
        details=details,
    )
