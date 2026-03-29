"""Profile-aware tool access policy helpers.

Infrastructure for profile-based tool blocking. The deny set is currently
empty — the response size guard provides uniform protection for all profiles.
The set and helpers are retained so tools can be hot-patched back into the
deny list without an architectural revert if needed.
"""

from __future__ import annotations

from typing import Final

CURSOR_SAFE_PROFILE: Final[str] = "cursor_safe"
DEFAULT_PROFILE: Final[str] = "default"

# Empty since response size guard (response_size_guard.py) now handles
# oversized responses uniformly. Kept as rollback infrastructure.
_CURSOR_DISPATCH_DENY: Final[frozenset[str]] = frozenset()


def is_dispatch_tool_allowed(profile: str, tool_name: str) -> bool:
    """Check whether a dispatch subtool is allowed under the active profile.

    Returns True unconditionally when the deny set is empty (current state).
    Retained as rollback infrastructure for the response size guard.
    """
    if profile != CURSOR_SAFE_PROFILE:
        return True
    return tool_name not in _CURSOR_DISPATCH_DENY


def dispatch_denial_reason(tool_name: str) -> str:
    """Return a user-facing denial reason for blocked dispatch calls."""
    return (
        f"Tool '{tool_name}' is disabled by dispatch policy. "
        "Use the corresponding bounded preview/detail tool."
    )
