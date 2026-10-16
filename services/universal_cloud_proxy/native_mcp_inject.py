"""MCP injection helpers for provider-native cloud-proxy routes."""

from __future__ import annotations

from collections.abc import Collection

from llm_adapters._mcp_entry import (
    ANTHROPIC_AGENTIC_MCP_TOOLS,
    AnthropicCallbackTokenError,
    anthropic_mcp_server_entry,
    anthropic_mcp_toolset,
    cache_control_breakpoint_count,
    normalize_mcp_public_url,
    openai_xai_mcp_tool_entry,
    resolve_anthropic_callback_token,
)
from universal_logging import get_logger

logger = get_logger(__name__)

_MCP_SERVER_NAME = "vortex"


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
        names = [part.strip() for part in raw.split(",") if part.strip()]
        # "X-ULG-MCP-Tools: ," parses to no names. That is an absent header,
        # not a request for zero tools.
        if names:
            return names
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
            anthropic_mcp_toolset(
                server_entry["name"],
                enabled_tools,
                existing_breakpoints=cache_control_breakpoint_count(body),
            ),
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
