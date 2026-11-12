"""Install box MCP launcher + header seeds into a Grok profile sand-data tree."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

from grokbot_box_mcp.connectors import connector_metadata
from grokbot_box_mcp.headers import (
    host_launcher_install_path,
    resolve_mcp_token_for_provision,
    write_host_header_seed,
)
from grokbot_box_mcp.window_units import GrokWindowProfile, load_window_profile

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER_SRC = _REPO_ROOT / "scripts" / "grokbot-mcp-remote-launcher.sh"


def _install_executable(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    mode = dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    dest.chmod(mode)


def provision_box_mcp_artifacts(
    profile: GrokWindowProfile,
    *,
    token: str | None = None,
    skip_header: bool = False,
) -> dict[str, object]:
    """Copy launcher into sand-data/bin and seed vortex-mcp-headers for this window."""
    if not _LAUNCHER_SRC.is_file():
        raise FileNotFoundError(f"launcher missing: {_LAUNCHER_SRC}")
    launcher_dest = host_launcher_install_path(profile.user_data_dir)
    _install_executable(_LAUNCHER_SRC, launcher_dest)

    header_path: str | None = None
    token_source = ""
    if not skip_header:
        if token is None:
            token, token_source = resolve_mcp_token_for_provision()
        else:
            token_source = "arg:token"
        header = write_host_header_seed(profile.user_data_dir, token)
        header_path = str(header)

    meta = connector_metadata()
    return {
        "ok": True,
        "slot": profile.slot,
        "user_data_dir": str(profile.user_data_dir),
        "launcher_host_path": str(launcher_dest),
        "header_host_path": header_path,
        "token_source": token_source if header_path else "",
        "connectors": meta,
    }


def provision_window_mcp(key: str, *, home: Path | None = None) -> dict[str, object]:
    profile = load_window_profile(key, home=home)
    return provision_box_mcp_artifacts(profile)
