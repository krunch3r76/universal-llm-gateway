"""Connector command strings for Grok Bot MCP stdio registration."""

from __future__ import annotations

from pathlib import Path

import yaml

from grokbot_box_mcp.headers import (
    HTTPS_PROXY_BOX,
    MCP_REMOTE_PINNED_VERSION,
    PROXYSHIM_NODE_REQUIRE,
    box_launcher_path,
)


def _gateway_mcp_urls() -> tuple[str, str]:
    """Read code + life URLs from ``~/.gateway/mcp.yaml`` (same source as Claude refresh)."""
    cfg_path = Path.home() / ".gateway" / "mcp.yaml"
    if not cfg_path.is_file():
        issuer = "https://mcp.k-1.me"
        return f"{issuer}/mcp/code/", f"{issuer}/mcp/life/"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    issuer = str((cfg.get("oauth") or {}).get("issuer", "https://mcp.k-1.me")).rstrip("/")
    code = str(cfg.get("mcp_server_url") or f"{issuer}/mcp/code").strip().rstrip("/")
    life = f"{issuer}/mcp/life"
    return f"{code}/", f"{life}/"


CODE_MCP_URL, LIFE_MCP_URL = _gateway_mcp_urls()


def connector_launcher_invocation(mcp_url: str, *, launcher: str | None = None) -> str:
    """Single box-path command the Grok connector UI should store."""
    exe = launcher or box_launcher_path()
    return f"{exe} {mcp_url.rstrip('/')}/"


def default_connector_command(surface: str = "code") -> str:
    url = CODE_MCP_URL if surface == "code" else LIFE_MCP_URL
    return connector_launcher_invocation(url)


def connector_metadata() -> dict[str, object]:
    """Provisioning summary for operators (no secrets)."""
    return {
        "mcp_remote_version": MCP_REMOTE_PINNED_VERSION,
        "launcher_path_box": box_launcher_path(),
        "code_command": default_connector_command("code"),
        "life_command": default_connector_command("life"),
        "https_proxy": HTTPS_PROXY_BOX,
        "node_options": PROXYSHIM_NODE_REQUIRE,
    }
