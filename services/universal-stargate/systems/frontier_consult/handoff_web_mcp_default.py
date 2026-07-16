"""Web-anthropic handoff life-on / code-off MCP default.

Life/web receivers use the ``/mcp/life`` surface. They can operate on Cortex
and coordinate on agent-bus, but cannot access the workspace checkout or
code-only operations. Continuity: a24222 over-grant; a24223 life-vs-code split.
"""

from __future__ import annotations

from .handoff_life_mirror import is_life_web_receiver

# Canonical Block-5 body for the life-only web receiver.
LIFE_ONLY_MCP_BODY = (
    "LIFE/CORTEX MCP: ON — default plan uses cortex(entity_get/search), "
    'agent_bus(fetch/reply), and fs(sandbox="cortex", op="read"). '
    "Life-surface writes require explicit task/output authority.\n"
    "CODE/VORTEX MCP: OFF — no workspaces sandbox, checkout/source access, "
    "or code-only tools (team_dispatch, panel_dispatch, pipeline, manage, "
    "observability)."
)


def has_explicit_life_code_split(body: str | None) -> bool:
    """Return whether Block 5 explicitly grants life MCP and denies code MCP."""
    if body is None:
        return False
    normalized = " ".join(body.upper().split())
    return "LIFE/CORTEX MCP: ON" in normalized and "CODE/VORTEX MCP: OFF" in normalized


def apply_web_mcp_default(
    text: str,
    *,
    to_agent: str | None,
    current_body: str | None,
    replace_body,
) -> tuple[str, bool]:
    """Stamp the explicit life-on / code-off contract for life/web.

    Returns ``(text, stamped)``.
    ``replace_body(text, tag, body)`` must replace a packet tag's inner body.
    """
    if not is_life_web_receiver(to_agent):
        return text, False
    if has_explicit_life_code_split(current_body):
        return text, False
    stamped = replace_body(text, "mcp_capabilities", f"\n{LIFE_ONLY_MCP_BODY}\n")
    return stamped, stamped != text
