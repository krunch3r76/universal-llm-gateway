"""Resolve and seed vortex MCP OAuth header files for the Grok Bot sand box."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

# Proven on gc1 healthy sand: box opens /home/box/.gateway/vortex-mcp-headers first.
BOX_HEADER_CANDIDATES: tuple[str, ...] = (
    "/home/box/.gateway/vortex-mcp-headers",
    "/home/box/sand-data/vortex-mcp-headers",
    "/home/box/.vortex-mcp-headers",
)

BOX_GATEWAY_HEADER_NAME = "vortex-mcp-headers"

BOX_LAUNCHER_BASENAME = "grokbot-mcp-remote-launcher.sh"
BOX_LAUNCHER_BOX_PATH = f"/home/box/.local/bin/{BOX_LAUNCHER_BASENAME}"
BOX_LAUNCHER_SAND_PATH = f"/home/box/sand-data/bin/{BOX_LAUNCHER_BASENAME}"

MCP_REMOTE_PINNED_VERSION = "0.8.6"
HTTPS_PROXY_BOX = "http://127.0.0.1:8791"
PROXYSHIM_NODE_REQUIRE = "--require /home/box/.proxyshim/preload.cjs"


def host_operator_gateway_header_path(home: Path | None = None) -> Path:
    """Host path that maps to ``/home/box/.gateway/vortex-mcp-headers`` (gc1-proven)."""
    home = home or Path.home()
    return home / ".gateway" / BOX_GATEWAY_HEADER_NAME


def host_sand_header_path(user_data_dir: Path) -> Path:
    """Fallback host sand-data seed (not synced on all windows; gc2 observed miss)."""
    return user_data_dir / "sand-data" / BOX_GATEWAY_HEADER_NAME


def host_primary_header_path(user_data_dir: Path, *, home: Path | None = None) -> Path:
    """Primary provision target: operator ``~/.gateway`` (not profile sand-data)."""
    _ = user_data_dir
    return host_operator_gateway_header_path(home)


def host_launcher_install_path(user_data_dir: Path) -> Path:
    """Host sand-data/bin copy (fallback path inside the box)."""
    return user_data_dir / "sand-data" / "bin" / BOX_LAUNCHER_BASENAME


def box_launcher_path() -> str:
    return BOX_LAUNCHER_BOX_PATH


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


def write_secure_text(path: Path, body: str, *, mode: int = 0o600) -> Path:
    """Create ``path`` with explicit mode (no world-readable window)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(path, flags, mode)
    try:
        os.write(fd, body.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def write_host_header_seed(
    user_data_dir: Path,
    token: str,
    *,
    home: Path | None = None,
    mode: int = 0o600,
    sand_fallback: bool = True,
) -> Path:
    """Write header to host ``~/.gateway``; optional sand-data copy as fallback."""
    home = home or Path.home()
    primary = host_primary_header_path(user_data_dir, home=home)
    write_secure_text(primary, format_header_file_body(token), mode=mode)
    if sand_fallback:
        sand = host_sand_header_path(user_data_dir)
        write_secure_text(sand, format_header_file_body(token), mode=mode)
    return primary


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


def append_proxyshim_node_options(existing: str | None) -> str:
    """Ensure proxyshim preload is present; never drop an existing NODE_OPTIONS value."""
    if not existing or not existing.strip():
        return PROXYSHIM_NODE_REQUIRE
    if PROXYSHIM_NODE_REQUIRE in existing:
        return existing
    return f"{existing} {PROXYSHIM_NODE_REQUIRE}"


def mcp_remote_env_defaults() -> dict[str, str]:
    return {
        "HTTPS_PROXY": HTTPS_PROXY_BOX,
        "NODE_OPTIONS": PROXYSHIM_NODE_REQUIRE,
    }
