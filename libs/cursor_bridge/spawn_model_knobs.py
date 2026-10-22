"""Merge cursor_bridge spawn_tab / spawn_house model knob inputs for Glass CDP."""

from __future__ import annotations


def _coerce_fast(value: bool | str | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


def merge_spawn_model_knobs(
    model_knobs: dict[str, str] | None,
    *,
    effort: str | None = None,
    fast: bool | str | None = None,
) -> dict[str, str] | None:
    """Build ``model_knobs`` for ``launch_cursor_agents_tab`` from MCP fields."""
    merged: dict[str, str] = {}
    if isinstance(model_knobs, dict):
        merged.update({str(k): str(v) for k, v in model_knobs.items()})
    if effort is not None and str(effort).strip():
        merged["effort"] = str(effort).strip()
    fast_bool = _coerce_fast(fast)
    if fast_bool is not None:
        merged["fast"] = "true" if fast_bool else "false"
    return merged or None


__all__ = ["merge_spawn_model_knobs"]
