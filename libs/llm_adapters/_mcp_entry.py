"""Shared helpers for provider-native remote-MCP entries.

Builds the per-provider MCP descriptor shape used both by the pipeline /
``frontier_dispatch`` path (via ``FrontierRequest.remote_mcp=True`` →
adapter ``build_frontier_request``) and by the cloud-proxy external-wire
path (``-mcp`` suffix detection in ``native_routes._forward_native``).

One helper per provider family — each returns the descriptor dict; the
calling site decides where to attach it (``body["mcp_servers"]`` for
Anthropic vs. appending to ``body["tools"]`` for OpenAI/xAI).

``resolve_mcp_env`` reads the two env vars required by Stargate
(``MCP_PUBLIC_URL`` + ``MCP_AUTH_TOKEN``) and raises a typed
``RuntimeError`` when either is missing, so the pipeline handler can
surface the misconfiguration via a dedicated signal.
"""

from __future__ import annotations

import os


class RemoteMcpEnvMissingError(RuntimeError):
    """Raised by ``resolve_mcp_env`` when MCP_PUBLIC_URL or MCP_AUTH_TOKEN is unset.

    Using a typed exception instead of a bare RuntimeError lets callers catch
    this specific misconfiguration without string-matching the message.
    """

    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            "remote_mcp requested but MCP_PUBLIC_URL/MCP_AUTH_TOKEN not "
            f"configured (missing: {', '.join(missing)})"
        )
        self.missing = missing


def resolve_mcp_env() -> tuple[str, str]:
    """Return (MCP_PUBLIC_URL, MCP_AUTH_TOKEN) or raise RemoteMcpEnvMissingError.

    Both env vars MUST be set for remote MCP to work. The cloud-proxy
    container gets them via its yaml config; the Stargate container gets
    them via docker-compose env passthrough (see Phase 0 compose change).
    """
    url = os.environ.get("MCP_PUBLIC_URL", "").strip()
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not url or not token:
        missing = []
        if not url:
            missing.append("MCP_PUBLIC_URL")
        if not token:
            missing.append("MCP_AUTH_TOKEN")
        raise RemoteMcpEnvMissingError(missing)
    return url, token


def anthropic_mcp_server_entry(
    url: str,
    token: str,
    *,
    name: str = "vortex",
) -> dict:
    """Anthropic ``mcp_servers`` entry — belongs in ``body["mcp_servers"]``.

    The ``authorization_token`` field triggers the server-side auth on the
    Anthropic side; the beta header is auto-wired by the Anthropic adapter
    when ``body["mcp_servers"]`` is populated.
    """
    return {
        "type": "url",
        "name": name,
        "url": url,
        "authorization_token": token,
    }


def openai_xai_mcp_tool_entry(
    url: str,
    token: str,
    *,
    name: str = "vortex",
    require_approval: str = "never",
) -> dict:
    """OpenAI/xAI Responses API ``tools[]`` entry of ``type="mcp"``.

    ``require_approval="never"`` makes OpenAI invoke tools without
    confirmation prompts; xAI ignores the field but tolerates it, so the
    shape is shared across both vendors.
    """
    return {
        "type": "mcp",
        "server_url": url,
        "server_label": name,
        "authorization": f"Bearer {token}",
        "require_approval": require_approval,
    }
