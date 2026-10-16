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
# This set is the read-only probe default, not the default for agentic -mcp.
ANTHROPIC_API_DEFAULT_MCP_TOOLS: frozenset[str] = frozenset({"rag", "fs", "cortex"})

# team_dispatch / frontier / CDP -mcp runs need these in addition to the probe set.
# Rule: enable team_dispatch and agent_bus unless the caller is an anthropic-api
# probe or opts into the rag/fs/cortex list.
ANTHROPIC_AGENTIC_MCP_TOOLS: frozenset[str] = ANTHROPIC_API_DEFAULT_MCP_TOOLS | {
    "team_dispatch",
    "agent_bus",
}

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


def enabled_tools_for_native_mcp(headers) -> Collection[str] | None:
    """Resolve the Anthropic tool list for one native ``-mcp`` request.

    An explicit ``X-ULG-MCP-Tools`` list wins. ``X-ULG-Client: anthropic-api``
    or ``X-ULG-MCP-Profile: probe`` returns None so inject uses the read-only
    rag/fs/cortex set. Every other caller, including pipeline ``team_dispatch``
    with no list, gets ``ANTHROPIC_AGENTIC_MCP_TOOLS``.
    """
    raw = ""
    client = ""
    profile = ""
    if headers is not None:
        raw = str(headers.get("x-ulg-mcp-tools") or "").strip()
        client = str(headers.get("x-ulg-client") or "").strip()
        profile = str(headers.get("x-ulg-mcp-profile") or "").strip().lower()
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]
    if client == "anthropic-api" or profile == "probe":
        return None
    return ANTHROPIC_AGENTIC_MCP_TOOLS


def inject_native_mcp(
    provider_key: str,
    body: dict,
    *,
    enabled_tools: Collection[str] | None = None,
) -> None:
    """Inject provider-appropriate MCP entry into a native request body.

    ``enabled_tools`` is the per-dispatch allowlist for Anthropic. ``None``
    is the read-only probe set (``ANTHROPIC_API_DEFAULT_MCP_TOOLS``). Callers
    that need agentic tools pass ``ANTHROPIC_AGENTIC_MCP_TOOLS`` or their own
    list. OpenAI and xAI ignore it.
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
