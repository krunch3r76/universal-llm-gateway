from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(slots=True, kw_only=True)
class FallbackEligibility:
    should_fallback: bool
    reason: str
    error_type: str


def classify_fallback_error(exc: Exception) -> FallbackEligibility:
    """Return whether another model could plausibly recover from this failure."""
    from .proxy_client import ProxyClientError

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
