"""Resolve and seed vortex MCP OAuth header files for the Grok Bot sand box."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

# Order matches Grok sand path rewrite: sand-data mount is what mcp-remote opens.
BOX_HEADER_CANDIDATES: tuple[str, ...] = (
    "/home/box/sand-data/vortex-mcp-headers",
    "/home/box/.gateway/vortex-mcp-headers",
    "/home/box/.vortex-mcp-headers",
)

BOX_LAUNCHER_BASENAME = "grokbot-mcp-remote-launcher.sh"
BOX_LAUNCHER_SAND_PATH = f"/home/box/sand-data/bin/{BOX_LAUNCHER_BASENAME}"

MCP_REMOTE_PINNED_VERSION = "0.8.6"


def host_primary_header_path(user_data_dir: Path) -> Path:
    """Host path that syncs to ``/home/box/sand-data/vortex-mcp-headers`` in the box."""
    return user_data_dir / "sand-data" / "vortex-mcp-headers"


def host_launcher_install_path(user_data_dir: Path) -> Path:
    """Host path for the box launcher (synced under sand-data/bin)."""
    return user_data_dir / "sand-data" / "bin" / BOX_LAUNCHER_BASENAME


def box_launcher_path() -> str:
    return BOX_LAUNCHER_SAND_PATH


def resolve_box_header_path(
    candidates: tuple[str, ...] | None = None,
    *,
    exists: Callable[[Path], bool] | None = None,
) -> tuple[str | None, list[str]]:
    """Return the first existing candidate path and the list checked (for stderr)."""
    checker = exists or (lambda p: p.is_file())
    checked: list[str] = []
    pool = candidates if candidates is not None else BOX_HEADER_CANDIDATES
    for raw in pool:
        checked.append(raw)
        if checker(Path(raw)):
            return raw, checked
    return None, checked


def format_header_file_body(token: str) -> str:
    token = token.strip()
    if not token:
        raise ValueError("MCP token is empty")
    return f"Authorization: Bearer {token}\n"


def write_host_header_seed(
    user_data_dir: Path,
    token: str,
    *,
    mode: int = 0o600,
) -> Path:
    """Write the header file into the profile sand-data tree (synced into the box)."""
    path = host_primary_header_path(user_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_header_file_body(token), encoding="utf-8")
    path.chmod(mode)
    return path


def resolve_mcp_token_for_provision() -> tuple[str, str]:
    """Host-side token for seeding; mirrors cursor-sdk bridge resolution."""
    from services.git_integration_worker.cursor_sdk_context import resolve_mcp_token

    token, source = resolve_mcp_token()
    if token:
        return token, source
    from services.git_integration_worker.cursor_home import operator_real_home

    home = operator_real_home()
    token, source = resolve_mcp_token(real_home=home)
    if not token:
        raise RuntimeError(
            "MCP token missing for grokbox header seed "
            f"(checked MCP_TOKEN / ~/.gateway/mcp.yaml; last_source={source})"
        )
    return token, source


def mcp_remote_env_defaults() -> dict[str, str]:
    return {
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY", "http://127.0.0.1:8791"),
        "NODE_OPTIONS": os.environ.get(
            "NODE_OPTIONS", "--require /home/box/.proxyshim/preload.cjs"
        ),
    }
