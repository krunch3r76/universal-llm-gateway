"""Classify whether a failed model invocation should trigger ranked fallback.

Used by DAG step runners and generate handlers when a primary model call fails.
Maps exceptions (proxy errors, transport timeouts, deterministic local faults)
to a ``FallbackEligibility`` verdict consumed by ``step_model_fallback`` and
``model_fallback`` handlers. Suppression reasons block fallback when routing
constraints (e.g. cloud-only primary on a local worker) would make retry futile.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from ..step_config import ResolvedTargetModel


@dataclass(slots=True, kw_only=True)
class FallbackEligibility:
    should_fallback: bool
    reason: str
    error_type: str


def classify_fallback_error(
    exc: Exception,
    *,
    suppression_reason: str | None = None,
) -> FallbackEligibility:
    """Return whether another model could plausibly recover from this failure."""
    from .proxy_client import ProxyClientError

    if suppression_reason:
        return FallbackEligibility(
            should_fallback=False,
            reason=suppression_reason,
            error_type=type(exc).__name__,
        )
    if isinstance(exc, ProxyClientError):
        return FallbackEligibility(
            should_fallback=True,
            reason="proxy_client_error",
            error_type=type(exc).__name__,
        )
    if isinstance(exc, TimeoutError | httpx.TimeoutException | httpx.TransportError):
        return FallbackEligibility(
            should_fallback=True,
            reason="upstream_timeout_or_transport_error",
            error_type=type(exc).__name__,
        )
    return FallbackEligibility(
        should_fallback=False,
        reason="deterministic_local_error",
        error_type=type(exc).__name__,
    )


def get_fallback_suppression_reason(
    *,
    primary_resolution: ResolvedTargetModel | None,
    model_requirements: dict[str, Any] | None,
) -> str | None:
    """Return why fallback must be suppressed for the resolved primary model."""
    if primary_resolution is None or not isinstance(model_requirements, dict):
        return None

    source = model_requirements.get("source")
    if source != "cloud":
        return None

    if primary_resolution.is_local:
        return "routing_layer_mismatch"

    return None
