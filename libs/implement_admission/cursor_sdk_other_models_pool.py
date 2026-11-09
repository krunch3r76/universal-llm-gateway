"""Other Models pool policy for cursor-sdk generate / GIW dispatch admit."""

from __future__ import annotations

from typing import Final, Literal

from cursor_capabilities import is_other_models_pool

from implement_admission.routing import load_route_policy

CursorSdkOtherModelsPoolMode = Literal["deny", "explicit_pin_only"]

_VALID_MODES: Final[frozenset[str]] = frozenset({"deny", "explicit_pin_only"})
_DEFAULT_MODE: CursorSdkOtherModelsPoolMode = "deny"


def load_cursor_sdk_other_models_pool_mode(
    path=None,
) -> CursorSdkOtherModelsPoolMode:
    """Read ``cursor_sdk_other_models_pool`` from route policy (default ``deny``)."""
    policy = load_route_policy(path)
    raw = (
        str(policy.get("cursor_sdk_other_models_pool") or _DEFAULT_MODE).strip().lower()
    )
    if raw not in _VALID_MODES:
        msg = (
            "cursor_sdk_other_models_pool must be one of "
            f"{sorted(_VALID_MODES)!r}, got {raw!r}"
        )
        raise ValueError(msg)
    return raw  # type: ignore[return-value]


def other_models_pool_generate_admitted(
    *,
    model_pin: str | None,
    resolved_model: str,
    path=None,
) -> bool:
    """Whether an Other-Models resolution may proceed on cursor-sdk generate/admit."""
    if not is_other_models_pool(resolved_model):
        return True
    mode = load_cursor_sdk_other_models_pool_mode(path)
    if mode == "explicit_pin_only":
        return bool((model_pin or "").strip())
    return False


__all__ = [
    "CursorSdkOtherModelsPoolMode",
    "load_cursor_sdk_other_models_pool_mode",
    "other_models_pool_generate_admitted",
]
