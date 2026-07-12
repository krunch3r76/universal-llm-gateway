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

from typing import Any, Final, Literal

from _derive import derive_cortex_surface
from cortex_gate_events import McpCortexOpRejected
from mcp_events import record

Surface = Literal["life", "code"]

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

_LifeSpecCache: Any | None = None
_CodeSpecCache: Any | None = None


def reset_endpoint_op_cache() -> None:
    """Test helper — invalidate module-level surface spec caches."""
    global _LifeSpecCache, _CodeSpecCache
    _LifeSpecCache = None
    _CodeSpecCache = None


def _surface_spec(surface: Surface):
    global _LifeSpecCache, _CodeSpecCache
    if surface == "life":
        if _LifeSpecCache is None:
            _LifeSpecCache = derive_cortex_surface("life")
        return _LifeSpecCache
    if _CodeSpecCache is None:
        _CodeSpecCache = derive_cortex_surface("code")
    return _CodeSpecCache


def _overflow_hint(family: str, op: str) -> str:
    if family == "admin":
        return (
            f"Op {op!r} is admin-family — not admitted on this surface. "
            "Use dispatch/tool_search overflow on the code seat."
        )
    return (
        f"Op {op!r} ({family} family) is not on this surface's cortex enum. "
        "Use dispatch/tool_search overflow on the code seat."
    )


def endpoint_op_allowed(
    surface: str,
    tool_name: str,
    op: str,
) -> tuple[bool, dict[str, Any] | None]:
    """Return (allowed, rejection_payload) for a cortex op on a surface."""
    if tool_name != "cortex":
        return True, None

    op = str(op or "").strip()
    if not op:
        return True, None

    if surface not in ("life", "code"):
        return True, None

    spec = _surface_spec(surface)  # type: ignore[arg-type]
    family = spec.families.get(op)
    if family is None:
        return True, None

    if op in spec.ops_enum:
        return True, None

    payload: dict[str, Any] = {
        "family": family,
        "surface": surface,
        "status_code": 422,
        "hint": _overflow_hint(family, op),
    }
    record(
        McpCortexOpRejected(surface=surface, family=family, op=op).signal,
        surface=surface,
        family=family,
        op=op,
    )
    return False, payload


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
