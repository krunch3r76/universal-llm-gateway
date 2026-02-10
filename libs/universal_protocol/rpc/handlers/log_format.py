"""Log formatting utilities for RPC handlers.

Single responsibility: Format log prefixes for request tracing.
"""

from typing import Any


def extract_request_id(params: dict[str, Any]) -> str | None:
    """Extract and remove request_id from params.

    Inputs:
        params: RPC parameters (mutated: _request_id removed)

    Outputs:
        request_id string or None
    """
    return params.pop("_request_id", None)


def get_correlation_id(params: dict[str, Any]) -> str:
    """Extract correlation ID from params for logging.

    Inputs:
        params: RPC parameters (not mutated)

    Outputs:
        correlation_id string or "unknown"
    """
    return params.get("correlation_id", "unknown")


def make_log_prefix(params: dict[str, Any]) -> str:
    """Create standard log prefix with request_id and correlation_id.

    Inputs:
        params: RPC parameters (mutated: _request_id removed)

    Outputs:
        Formatted log prefix string

    Side-effect:
        Removes _request_id from params (routing-only field)
    """
    correlation_id = get_correlation_id(params)
    request_id = extract_request_id(params)
    return f"[request_id={request_id}][correlation_id={correlation_id}]"

