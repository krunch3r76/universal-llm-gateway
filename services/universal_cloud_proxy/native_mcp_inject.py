"""MCP injection helpers for provider-native cloud-proxy routes."""

from __future__ import annotations

from collections.abc import Collection

from llm_adapters._mcp_entry import (
    AnthropicCallbackTokenError,
    anthropic_mcp_server_entry,
    normalize_mcp_public_url,
    openai_xai_mcp_tool_entry,
    resolve_anthropic_callback_token,
)
from universal_logging import get_logger

logger = get_logger(__name__)

_MCP_SERVER_NAME = "vortex"

# anthropic-api call allowlist names (ops stay enforced in tool_access.py).
# Absence of an explicit list uses this set, not the full primary catalog.
ANTHROPIC_API_DEFAULT_MCP_TOOLS: frozenset[str] = frozenset({"rag", "fs", "cortex"})

# False restores the pre-39434 toolset: every tool enabled, no cache breakpoint.
_APPLY_ANTHROPIC_TOOLSET_POLICY = True


def _anthropic_mcp_toolset(
    server_name: str,
    enabled_tools: Collection[str] | None,
) -> dict:
    """Build the Anthropic mcp_toolset entry.

    With the policy on, disabled-by-default plus per-tool configs (lever 1)
    and an ephemeral cache breakpoint on the toolset (lever 3). With the
    policy off, the bare toolset Anthropic expands to every listed tool.
    """
    entry: dict = {"type": "mcp_toolset", "mcp_server_name": server_name}
    if not _APPLY_ANTHROPIC_TOOLSET_POLICY:
        return entry
    names = (
        ANTHROPIC_API_DEFAULT_MCP_TOOLS
        if enabled_tools is None
        else frozenset(enabled_tools)
    )
    entry["default_config"] = {"enabled": False}
    entry["configs"] = {name: {"enabled": True} for name in sorted(names)}
    entry["cache_control"] = {"type": "ephemeral"}
    return entry


def inject_native_mcp(
    provider_key: str,
    body: dict,
    *,
    enabled_tools: Collection[str] | None = None,
) -> None:
    """Inject provider-appropriate MCP entry into a native request body.

    ``enabled_tools`` is the per-dispatch allowlist for Anthropic. ``None``
    uses ``ANTHROPIC_API_DEFAULT_MCP_TOOLS``. An explicit collection replaces
    that default (including names outside it). OpenAI and xAI ignore it.
    """
    from .cloud_proxy import _get_mcp_executor

    executor = _get_mcp_executor()
    if executor is None:
        return

    config = get_mcp_config_for_provider(provider_key)
    if not config:
        return

    url = config.get("url") or ""
    if provider_key == "anthropic":
        if not url:
            return
        try:
            token = resolve_anthropic_callback_token()
        except AnthropicCallbackTokenError:
            return
        config_token = str(config.get("token") or "").strip()
        if config_token and config_token == token:
            return
        existing = body.get("mcp_servers") or []
        server_entry = anthropic_mcp_server_entry(
            normalize_mcp_public_url(url), token, name=_MCP_SERVER_NAME
        )
        body["mcp_servers"] = [*existing, server_entry]
        body["tools"] = [
            *(body.get("tools") or []),
            _anthropic_mcp_toolset(server_entry["name"], enabled_tools),
        ]
        return

    if not config.get("token"):
        return

    token = config["token"]

    if provider_key in {"openai", "xai"}:
        if provider_key == "xai":
            logger.info(
                "remote MCP injection skipped — xAI does not yet support type:mcp"
            )
            return
        existing_tools = body.get("tools") or []
        body["tools"] = [
            *existing_tools,
            openai_xai_mcp_tool_entry(url, token, name=_MCP_SERVER_NAME),
        ]


def get_mcp_config_for_provider(provider_key: str) -> dict | None:
    """Resolve MCP server URL and auth token for a provider."""
    from .cloud_proxy import app

    config = getattr(app.state, "config", None)
    if config is None:
        return None

    for p in config.providers:
        if p.provider == provider_key and p.mcp_server_url:
            return {"url": p.mcp_server_url, "token": p.mcp_auth_token}

    if config.mcp_server_url:
        return {"url": config.mcp_server_url, "token": config.mcp_auth_token}
    return None
