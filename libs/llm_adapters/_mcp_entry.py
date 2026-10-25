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
from collections.abc import Collection
from pathlib import Path
from typing import Any

import yaml
from transport_utils.gateway_dir import resolve_gateway_dir

logger = logging.getLogger(__name__)

# Bare ``…/mcp`` is a retired dual-endpoint mount (404). Live mounts are
# ``/mcp/code`` (coding/API bridge) and ``/mcp/life`` (claude.ai life bridge).
# Stargate agent_seat client-side injection must hit a live mount or tools/list
# returns empty and falls back to STATIC_TOOL_FALLBACK (cortex+agent_bus, no fs).
_DEFUNCT_BARE_MCP_SUFFIX = "/mcp"


class AnthropicCallbackTokenError(RuntimeError):
    """Raised when the dedicated Anthropic MCP callback token is missing or collides."""

    def __init__(self, message: str, *, keys: list[str] | None = None) -> None:
        super().__init__(message)
        self.keys = keys or []


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


def _mcp_config_path() -> Path:
    configured = os.environ.get("MCP_CONFIG_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return resolve_gateway_dir() / "mcp.yaml"


def _load_mcp_yaml_raw() -> dict[str, Any]:
    path = _mcp_config_path()
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _yaml_resolved_admin_token(raw: dict[str, Any]) -> str:
    token_env_name = raw.get("auth_token_env")
    if isinstance(token_env_name, str) and token_env_name.strip():
        env_val = os.environ.get(token_env_name.strip(), "").strip()
        if env_val:
            return env_val
    raw_token = raw.get("auth_token", "")
    if isinstance(raw_token, str):
        return raw_token.strip()
    return ""


def anthropic_callback_collides_with_admin(
    callback: str,
    raw: dict[str, Any] | None = None,
    *,
    mcp_auth_token: str | None = None,
) -> bool:
    """True when ``callback`` equals a configured admin bearer (keys only in errors)."""
    if not callback:
        return True
    env_admin = (mcp_auth_token if mcp_auth_token is not None else os.environ.get("MCP_AUTH_TOKEN", "")).strip()
    if env_admin and callback == env_admin:
        return True
    data = raw if raw is not None else _load_mcp_yaml_raw()
    yaml_admin = _yaml_resolved_admin_token(data)
    return bool(yaml_admin and callback == yaml_admin)


def resolve_anthropic_callback_token() -> str:
    """Return the Anthropic API MCP callback bearer (never log the value)."""
    env_val = os.environ.get("MCP_ANTHROPIC_CALLBACK_TOKEN", "").strip()
    if env_val:
        token = env_val
    else:
        raw = _load_mcp_yaml_raw()
        yaml_val = raw.get("anthropic_callback_token", "")
        token = str(yaml_val).strip() if yaml_val is not None else ""
    if not token:
        raise AnthropicCallbackTokenError(
            "anthropic callback token is empty",
            keys=["MCP_ANTHROPIC_CALLBACK_TOKEN", "anthropic_callback_token"],
        )
    raw = _load_mcp_yaml_raw()
    if anthropic_callback_collides_with_admin(token, raw):
        raise AnthropicCallbackTokenError(
            "anthropic callback token collides with admin MCP credentials",
            keys=["MCP_AUTH_TOKEN", "auth_token", "auth_token_env"],
        )
    return token


def anthropic_callback_yaml_backfill(
    raw: dict[str, Any], env: dict[str, str]
) -> str | None:
    """Yaml ``anthropic_callback_token`` for ``build_service_env`` when env unset."""
    if env.get("MCP_ANTHROPIC_CALLBACK_TOKEN", "").strip():
        return None
    candidate = raw.get("anthropic_callback_token", "")
    token = str(candidate).strip() if candidate is not None else ""
    if not token:
        return None
    if anthropic_callback_collides_with_admin(
        token, raw, mcp_auth_token=env.get("MCP_AUTH_TOKEN", "")
    ):
        return None
    return token


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


# anthropic-api call allowlist names (ops stay enforced in tool_access.py).
# This set is the read-only probe default, not the default for agentic -mcp.
ANTHROPIC_API_DEFAULT_MCP_TOOLS: frozenset[str] = frozenset(
    {"rag", "fs", "cortex", "retrieve"}
)

# team_dispatch / frontier / CDP -mcp runs need these in addition to the probe set.
ANTHROPIC_AGENTIC_MCP_TOOLS: frozenset[str] = ANTHROPIC_API_DEFAULT_MCP_TOOLS | {
    "team_dispatch",
    "agent_bus",
    "agent_bus_read",
}

# False restores the pre-39434 toolset on every caller of anthropic_mcp_toolset:
# every tool enabled, no cache breakpoint. Covers cloud-proxy inject and the
# frontier adapter.
_APPLY_ANTHROPIC_TOOLSET_POLICY = True


def cache_control_breakpoint_count(node: Any) -> int:
    """Count ``cache_control`` keys already present in a request body."""
    if isinstance(node, dict):
        found = 1 if "cache_control" in node else 0
        return found + sum(cache_control_breakpoint_count(v) for v in node.values())
    if isinstance(node, list):
        return sum(cache_control_breakpoint_count(v) for v in node)
    return 0


def anthropic_mcp_toolset(
    server_name: str,
    enabled_tools: Collection[str] | None,
    *,
    existing_breakpoints: int = 0,
) -> dict:
    """Build the Anthropic mcp_toolset entry shared by inject and frontier.

    With the policy on, disabled-by-default plus per-tool configs (lever 1)
    and an ephemeral cache breakpoint on the toolset (lever 3). With the
    policy off, the bare toolset Anthropic expands to every listed tool.
    ``enabled_tools is None`` selects the read-only probe set.
    A body that already has four breakpoints does not get another one.
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
    if existing_breakpoints < 4:
        entry["cache_control"] = {"type": "ephemeral"}
    return entry
