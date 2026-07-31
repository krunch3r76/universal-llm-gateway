"""Unit tests for OAuth-client tool allowlist (grok-connector read-only)."""

from __future__ import annotations

from tool_access import (
    GROK_CONNECTOR_CLIENT_ID,
    oauth_client_denial_reason,
    oauth_client_tool_allowed,
)


def test_non_grok_client_unrestricted() -> None:
    assert oauth_client_tool_allowed("claude-ai", "manage", {"action": "status"})
    assert oauth_client_tool_allowed(None, "cortex", {"tool": "assert"})
    assert oauth_client_tool_allowed("", "fs", {"op": "write"})


def test_grok_connector_allows_cortex_stats() -> None:
    assert oauth_client_tool_allowed(
        GROK_CONNECTOR_CLIENT_ID, "cortex", {"tool": "stats"}
    )


def test_grok_connector_denies_cortex_writes() -> None:
    assert not oauth_client_tool_allowed(
        GROK_CONNECTOR_CLIENT_ID, "cortex", {"tool": "assert"}
    )
    assert not oauth_client_tool_allowed(
        GROK_CONNECTOR_CLIENT_ID, "cortex", {"tool": "entity_create"}
    )


def test_grok_connector_allows_agent_bus_read() -> None:
    assert oauth_client_tool_allowed(
        GROK_CONNECTOR_CLIENT_ID, "agent_bus_read", {"tool": "fetch"}
    )


def test_grok_connector_denies_mutating_primaries() -> None:
    for tool in ("manage", "fs", "agent_bus", "team_dispatch", "cortex_brief"):
        assert not oauth_client_tool_allowed(GROK_CONNECTOR_CLIENT_ID, tool, {})


def test_denial_reason_names_client_and_tool() -> None:
    reason = oauth_client_denial_reason(GROK_CONNECTOR_CLIENT_ID, "manage")
    assert GROK_CONNECTOR_CLIENT_ID in reason
    assert "manage" in reason
