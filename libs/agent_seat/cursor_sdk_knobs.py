"""cursor-sdk ModelSelection knob overrides from handoff executor tier."""

from __future__ import annotations

_COMPOSER_FAST = "composer-fast"
_COMPOSER_THINKING = "composer-thinking"


def derive_model_knobs(
    *,
    executor_override: str | None = None,
    packet_executor_override: str | None = None,
) -> dict[str, str] | None:
    """Map handoff ``executor_override`` to cursor-sdk knob overrides.

    When ``None`` is returned the worker applies registry defaults (``fast=true``).
    """
    override = (
        executor_override
        if executor_override is not None
        else packet_executor_override
    )
    if override == _COMPOSER_FAST:
        return {"fast": "true"}
    if override == _COMPOSER_THINKING:
        return {"fast": "false"}
    return None
