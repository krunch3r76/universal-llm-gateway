"""Classify HTTP error envelopes as capacity-class vs structural."""

from __future__ import annotations

import httpx

_CAPACITY_CLASS_ENVELOPE_CODES: frozenset[str] = frozenset(
    {
        "REQUEST_TIMEOUT",
        "INFERENCE_TIMEOUT",
        "LOAD_TIMEOUT",
        "NO_FEASIBLE_GATEWAY",
        "MODEL_LOADING",
        "RESOURCE_UNAVAILABLE",
        "GATEWAY_DISCONNECTED",
    }
)


def is_capacity_class_envelope(exc: httpx.HTTPStatusError) -> bool:
    """Return True iff an HTTP error body carries a capacity-class code."""
    try:
        body = exc.response.json()
    except (ValueError, TypeError):
        return False
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return False
    code = detail.get("code")
    return isinstance(code, str) and code in _CAPACITY_CLASS_ENVELOPE_CODES
