"""IDE-parity agent context for cursor-sdk bridge dispatches.

Loads the same ambient Cursor settings layers as the IDE Composer seat:
project rules (repo + parent traversal), user rules, team/plugins, and the
vortex MCP stdio surface used in ``~/.cursor/mcp.json``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from cursor_sdk.types import (
    AgentOptions,
    LocalAgentOptions,
    ModelSelection,
    StdioMcpServerConfig,
)

_VORTEX_MCP_SERVER = "user-vortex"
_STDIO_PROXY_RELPATH = Path("scripts/mcp-stdio-proxy.py")
_SETTING_SOURCES: tuple[str, ...] = ("all",)
_MCP_YAML_REL = Path(".gateway") / "mcp.yaml"
_CURSOR_XDG_AUTH = Path(".config") / "cursor" / "auth.json"


class CursorSdkParityError(ValueError):
    """Pre-flight parity failure — fail closed at dispatch admit."""


def _operator_home(real_home: Path | str | None = None) -> Path:
    if real_home is not None:
        return Path(real_home).expanduser()
    return Path(os.environ.get("HOME") or "~").expanduser()


def _read_mcp_yaml_scalar(home: Path, key: str) -> str:
    yaml_path = home / _MCP_YAML_REL
    if not yaml_path.is_file():
        return ""
    prefix = f"{key}:"
    for raw_line in yaml_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith(prefix):
            continue
        value = line.split(":", 1)[1].split("#", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value.strip()
    return ""


def resolve_mcp_token(*, real_home: Path | str | None = None) -> tuple[str, str]:
    """Return ``(token, source_label)`` for vortex MCP auth."""
    for env_key in ("MCP_TOKEN", "MCP_AUTH_TOKEN"):
        value = os.environ.get(env_key, "").strip()
        if value:
            return value, f"env:{env_key}"

    home = _operator_home(real_home)
    token_env_name = _read_mcp_yaml_scalar(home, "auth_token_env").strip()
    if token_env_name:
        from_env = os.environ.get(token_env_name, "").strip()
        if from_env:
            return from_env, f"env:{token_env_name}"

    from_yaml = _read_mcp_yaml_scalar(home, "auth_token").strip()
    if from_yaml:
        return from_yaml, "yaml:auth_token"

    return "", ""


def resolve_cursor_auth_source(*, real_home: Path | str | None = None) -> str:
    if os.environ.get("CURSOR_API_KEY", "").strip():
        return "env:CURSOR_API_KEY"
    auth_path = _operator_home(real_home) / _CURSOR_XDG_AUTH
    if auth_path.is_file():
        return f"file:{auth_path}"
    return ""


def validate_dispatch_context(
    source_repo: Path,
    *,
    real_home: Path | str | None = None,
) -> dict[str, object]:
    """Verify IDE-parity substrate before admitting a dispatch."""
    home = _operator_home(real_home)
    proxy = (source_repo / _STDIO_PROXY_RELPATH).resolve()
    if not proxy.is_file():
        raise CursorSdkParityError(f"vortex MCP proxy missing: {proxy}")

    token, token_source = resolve_mcp_token(real_home=home)
    if not token:
        raise CursorSdkParityError(
            "MCP token not configured — set MCP_TOKEN/MCP_AUTH_TOKEN or "
            f"{home / _MCP_YAML_REL} auth_token/auth_token_env"
        )

    cursor_auth = resolve_cursor_auth_source(real_home=home)
    if not cursor_auth:
        raise CursorSdkParityError(
            "Cursor credential not configured — set CURSOR_API_KEY or "
            f"seed {home / _CURSOR_XDG_AUTH}"
        )

    user_rules = home / ".cursor" / "rules"
    return {
        "setting_sources": list(_SETTING_SOURCES),
        "mcp_server": _VORTEX_MCP_SERVER,
        "mcp_proxy": str(proxy),
        "mcp_token_source": token_source,
        "cursor_auth_source": cursor_auth,
        "user_rules_dir_present": user_rules.is_dir(),
    }


def _resolve_mcp_token_env(*, real_home: Path | str | None = None) -> dict[str, str]:
    """Env vars for the stdio MCP proxy (HOME may be dispatch-isolated)."""
    env: dict[str, str] = {}
    mcp_url = os.environ.get("MCP_URL", "").strip()
    if mcp_url:
        env["MCP_URL"] = mcp_url
    token, _source = resolve_mcp_token(real_home=real_home)
    if token:
        env["MCP_TOKEN"] = token
    return env


def build_local_agent_options(dispatch_workspace: Path) -> LocalAgentOptions:
    """Mirror IDE Composer ambient settings; cwd = dispatch write-surface anchor."""
    return LocalAgentOptions(
        cwd=str(dispatch_workspace.resolve()),
        setting_sources=_SETTING_SOURCES,
    )


def build_mcp_servers(
    source_repo: Path,
    *,
    real_home: Path | str | None = None,
) -> dict[str, StdioMcpServerConfig]:
    """Stdio vortex MCP — same transport as IDE ``mcp.json`` fallback."""
    proxy = (source_repo / _STDIO_PROXY_RELPATH).resolve()
    if not proxy.is_file():
        raise FileNotFoundError(f"vortex MCP proxy missing: {proxy}")
    env = _resolve_mcp_token_env(real_home=real_home)
    return {
        _VORTEX_MCP_SERVER: StdioMcpServerConfig(
            command=sys.executable,
            args=[str(proxy)],
            env=env or None,
            cwd=str(source_repo.resolve()),
        )
    }


def build_agent_options(
    source_repo: Path,
    dispatch_workspace: Path,
    model: ModelSelection,
    *,
    real_home: Path | str | None = None,
) -> AgentOptions:
    """Full create_agent options for IDE-parity cursor-sdk dispatch."""
    return AgentOptions(
        model=model,
        mode="agent",
        local=build_local_agent_options(dispatch_workspace),
        mcp_servers=build_mcp_servers(source_repo, real_home=real_home),
    )
