"""Sidecar usage block — model_label beside token counts for rate-join (7119 L5)."""

from __future__ import annotations

from typing import Any

from services.git_integration_worker.cursor_sdk_usage_normalize import public_usage

_USAGE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def normalize_model_label(resolved_model: str | None) -> str | None:
    """Return a non-empty slug-style model label for emit, or None."""
    if not isinstance(resolved_model, str):
        return None
    label = resolved_model.strip()
    return label or None


def stamp_usage_model_label(
    usage: dict[str, Any] | None,
    resolved_model: str | None,
) -> dict[str, Any] | None:
    """Copy usage with ``model_label`` set when a resolved model is known."""
    label = normalize_model_label(resolved_model)
    if label is None:
        return public_usage(usage) if usage is not None else None
    base = dict(public_usage(usage) or {})
    base["model_label"] = label
    return base


def structured_closeout_has_usage_model_label(payload: dict[str, Any]) -> bool:
    """True when structured closeout JSON carries model_label in the usage block."""
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return False
    label = usage.get("model_label")
    return isinstance(label, str) and bool(label.strip())


def render_usage_sidecar_section(
    *,
    usage: dict[str, Any] | None,
    usage_capture_status: str | None,
    resolved_model: str | None,
) -> str | None:
    """Render ``## usage`` markdown beside token counts; None when no usage row."""
    stamped = stamp_usage_model_label(usage, resolved_model)
    if stamped is None:
        return None
    lines = ["## usage", ""]
    label = stamped.get("model_label")
    if isinstance(label, str) and label.strip():
        lines.append(f"model_label: {label.strip()}")
    for key in _USAGE_TOKEN_KEYS:
        if key in stamped:
            lines.append(f"{key}: {stamped[key]}")
    if usage_capture_status is not None:
        lines.append(f"usage_capture_status: {usage_capture_status}")
    if len(lines) <= 2:
        return None
    return "\n".join(lines)


__all__ = [
    "normalize_model_label",
    "render_usage_sidecar_section",
    "stamp_usage_model_label",
    "structured_closeout_has_usage_model_label",
]
