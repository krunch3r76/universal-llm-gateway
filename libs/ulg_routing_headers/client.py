"""Outbound routing headers for internal cortex-api ``POST /dispatch`` callers."""

from __future__ import annotations


def internal_dispatch_headers(
    *,
    surface: str,
    seat: str,
    caller: str,
) -> dict[str, str]:
    """Headers for a direct (non-MCP-adapter) cortex-api dispatch relay."""
    return {
        "X-ULG-Surface": surface,
        "X-ULG-Seat": seat,
        "X-ULG-Caller": caller,
    }
