"""Tier and timeout resolution for cursorbuild dispatch."""

from __future__ import annotations

from dataclasses import dataclass

from cursorbuild.constants import (
    DEFAULT_TIMEOUT_SECONDS,
    _VALID_TIERS,
    default_model_for_tier,
)


@dataclass(frozen=True, slots=True)
class _ResolvedParams:
    tier: str
    model: str
    timeout_seconds: int | None


def _resolve_params(
    *,
    tier: str,
    model: str | None,
    timeout_seconds: int | None,
) -> _ResolvedParams:
    if tier not in _VALID_TIERS:
        raise KeyError(tier)
    resolved_model = model if model is not None else default_model_for_tier(tier)
    resolved_timeout = (
        None
        if timeout_seconds == 0
        else (
            timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS
        )
    )
    return _ResolvedParams(
        tier=tier,
        model=resolved_model,
        timeout_seconds=resolved_timeout,
    )
