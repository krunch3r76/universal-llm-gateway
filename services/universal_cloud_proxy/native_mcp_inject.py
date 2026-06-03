"""MCP injection helpers for provider-native cloud-proxy routes."""

from __future__ import annotations

from llm_adapters._mcp_entry import (
    anthropic_mcp_server_entry,
    openai_xai_mcp_tool_entry,
)
from universal_logging import get_logger

logger = get_logger(__name__)

_MCP_SERVER_NAME = "vortex"


def inject_native_mcp(provider_key: str, body: dict) -> None:
    """Inject provider-appropriate MCP entry into a native request body."""
    from .cloud_proxy import _get_mcp_executor

    executor = _get_mcp_executor()
    if executor is None:
        return

    config = get_mcp_config_for_provider(provider_key)
    if not config or not config.get("token"):
        return

    url = config["url"]
    token = config["token"]

    if provider_key == "anthropic":
        existing = body.get("mcp_servers") or []
        body["mcp_servers"] = [
            *existing,
            anthropic_mcp_server_entry(url, token, name=_MCP_SERVER_NAME),
        ]
    elif provider_key in {"openai", "xai"}:
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
