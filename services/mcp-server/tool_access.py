"""Profile-aware tool access policy helpers."""

from __future__ import annotations

from typing import Final

CURSOR_SAFE_PROFILE: Final[str] = "cursor_safe"
DEFAULT_PROFILE: Final[str] = "default"

_CURSOR_DISPATCH_DENY: Final[frozenset[str]] = frozenset(
    {
        "agent_bus_fetch",
        "agent_bus_threads",
        "rag_search",
        "rag_answer",
        "query_observability",
    }
)


def is_dispatch_tool_allowed(profile: str, tool_name: str) -> bool:
    """Return whether a dispatch subtool is allowed for the active profile."""
    if profile != CURSOR_SAFE_PROFILE:
        return True
    return tool_name not in _CURSOR_DISPATCH_DENY


def dispatch_denial_reason(tool_name: str) -> str:
    """Return a stable user-facing denial reason for blocked dispatch calls."""
    return (
        f"Tool '{tool_name}' is disabled for cursor_safe profile. "
        "Use the corresponding bounded preview/detail tool."
    )
