"""Shared helpers for provider-native remote-MCP entries.

Builds the per-provider MCP descriptor shape used both by the pipeline /
``frontier_dispatch_v1`` pipeline path (via ``FrontierRequest.remote_mcp=True`` →
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

import logging
import os

logger = logging.getLogger(__name__)

# Bare ``…/mcp`` is a retired dual-endpoint mount (404). Live mounts are
# ``/mcp/code`` (coding/API bridge) and ``/mcp/life`` (claude.ai life bridge).
# Stargate agent_seat client-side injection must hit a live mount or tools/list
# returns empty and falls back to STATIC_TOOL_FALLBACK (cortex+agent_bus, no fs).
_DEFUNCT_BARE_MCP_SUFFIX = "/mcp"


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


def normalize_mcp_public_url(url: str) -> str:
    """Rewrite defunct bare ``/mcp`` and ensure a live mount has a trailing slash.

    Friction 24366: Stargate with ``MCP_PUBLIC_URL=…/mcp`` discovered zero tools
    and silently fell back to a cortex+agent_bus surface, so API reviewers lacked
    ``fs``. Bare ``…/mcp`` is 404; ``…/mcp/code`` without a trailing slash 307s to
    ``…/mcp/code/`` and ``McpToolExecutor`` (no redirect follow) treats that as
    discovery failure. Explicit ``/mcp/life`` and ``/mcp/code`` mounts are kept
    and slash-normalized.
    """
    stripped = url.strip().rstrip("/")
    if not stripped:
        return url
    if stripped.endswith("/mcp/code") or stripped.endswith("/mcp/life"):
        return f"{stripped}/"
    if stripped.endswith(_DEFUNCT_BARE_MCP_SUFFIX):
        rewritten = f"{stripped}/code/"
        logger.warning(
            "MCP_PUBLIC_URL bare mount %r is defunct; rewriting to %r "
            "(live mounts are /mcp/code/ and /mcp/life/)",
            url,
            rewritten,
        )
        return rewritten
    return stripped


def resolve_mcp_env() -> tuple[str, str]:
    """Return (MCP_PUBLIC_URL, MCP_AUTH_TOKEN) or raise RemoteMcpEnvMissingError.

    Both env vars MUST be set for remote MCP to work. The cloud-proxy
    container gets them via its yaml config; the Stargate container gets
    them via docker-compose env passthrough (see Phase 0 compose change).
    Bare ``…/mcp`` is normalized to ``…/mcp/code`` (see ``normalize_mcp_public_url``).
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
    return normalize_mcp_public_url(url), token


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
