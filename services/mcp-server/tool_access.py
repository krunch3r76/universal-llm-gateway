"""Profile-aware and OAuth-client tool access policy helpers.

Infrastructure for profile-based tool blocking. The deny set is currently
empty — the response size guard provides uniform protection for all profiles.
The set and helpers are retained so tools can be hot-patched back into the
deny list without an architectural revert if needed.

Also enforces a hard server-side allowlist for the dedicated
``grok-connector`` OAuth client used by xAI remote-MCP (arm 2B). Client-side
``allowed_tools`` is advisory only; this module is the authoritative gate.
"""

from __future__ import annotations

from typing import Any, Final

CURSOR_SAFE_PROFILE: Final[str] = "cursor_safe"
DEFAULT_PROFILE: Final[str] = "default"

# Empty since response size guard (response_size_guard.py) now handles
# oversized responses uniformly. Kept as rollback infrastructure.
_CURSOR_DISPATCH_DENY: Final[frozenset[str]] = frozenset()

# xAI remote-MCP inbound probe client — read-only surface only.
# See todo:xai-remote-mcp-inbound-setup / decision:xai-remote-mcp-inbound-access.
GROK_CONNECTOR_CLIENT_ID: Final[str] = "grok-connector"
GROK_CONNECTOR_ALLOWED_TOOLS: Final[frozenset[str]] = frozenset(
    {"cortex", "agent_bus_read"}
)
GROK_CONNECTOR_CORTEX_OPS: Final[frozenset[str]] = frozenset({"stats"})


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


def oauth_client_tool_allowed(
    client_id: str | None,
    tool_name: str,
    tool_args: dict[str, Any] | None = None,
) -> bool:
    """Return True iff ``client_id`` may invoke ``tool_name`` (with args).

    Non-``grok-connector`` clients are unrestricted here (other auth paths
    apply). ``grok-connector`` is limited to read-only MCP tools; for
    ``cortex``, only the ``stats`` sub-op is admitted.
    """
    if not client_id or client_id != GROK_CONNECTOR_CLIENT_ID:
        return True
    if tool_name not in GROK_CONNECTOR_ALLOWED_TOOLS:
        return False
    if tool_name == "cortex":
        op = str((tool_args or {}).get("tool") or "").strip()
        return op in GROK_CONNECTOR_CORTEX_OPS
    return True


def oauth_client_denial_reason(client_id: str, tool_name: str) -> str:
    """User-facing denial for OAuth-client allowlist rejects."""
    return (
        f"Tool '{tool_name}' is not permitted for oauth client "
        f"'{client_id}' (read-only allowlist)."
    )
