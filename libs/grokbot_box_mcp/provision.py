"""Install box MCP launcher + header seeds for Grok Bot windows."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

from grokbot_box_mcp.connectors import connector_metadata
from grokbot_box_mcp.headers import (
    host_launcher_install_path,
    host_operator_gateway_header_path,
    host_sand_header_path,
    resolve_mcp_token_for_provision,
    write_host_header_seed,
)
from grokbot_box_mcp.window_units import GrokWindowProfile, load_window_profile

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER_SRC = _REPO_ROOT / "scripts" / "grokbot-mcp-remote-launcher.sh"
_BOX_INSTALL_SRC = _REPO_ROOT / "scripts" / "grokbot-box-mcp-install.sh"


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
    home: Path | None = None,
) -> dict[str, object]:
    """Host: seed ~/.gateway header (box-proven path) + sand-data launcher fallback."""
    home = home or Path.home()
    if not _LAUNCHER_SRC.is_file():
        raise FileNotFoundError(f"launcher missing: {_LAUNCHER_SRC}")
    launcher_dest = host_launcher_install_path(profile.user_data_dir)
    _install_executable(_LAUNCHER_SRC, launcher_dest)
    box_install_dest = (
        profile.user_data_dir / "sand-data" / "bin" / "grokbot-box-mcp-install.sh"
    )
    if _BOX_INSTALL_SRC.is_file():
        _install_executable(_BOX_INSTALL_SRC, box_install_dest)

    header_path: str | None = None
    token_source = ""
    sand_header: str | None = None
    if not skip_header:
        if token is None:
            token, token_source = resolve_mcp_token_for_provision()
        else:
            token_source = "arg:token"
        header = write_host_header_seed(profile.user_data_dir, token, home=home)
        header_path = str(header)
        sand_header = str(host_sand_header_path(profile.user_data_dir))

    meta = connector_metadata()
    return {
        "ok": True,
        "slot": profile.slot,
        "user_data_dir": str(profile.user_data_dir),
        "header_host_path": header_path,
        "header_host_gateway": str(host_operator_gateway_header_path(home)),
        "header_host_sand_fallback": sand_header,
        "launcher_host_path": str(launcher_dest),
        "box_install_host_path": str(box_install_dest)
        if _BOX_INSTALL_SRC.is_file()
        else None,
        "token_source": token_source if header_path else "",
        "connectors": meta,
        "host_step_note": (
            "Write ~/.gateway/vortex-mcp-headers on io (maps to /home/box/.gateway/...); "
            "in-box: sand-data/bin/grokbot-box-mcp-install.sh --header-stdin"
        ),
    }


def provision_window_mcp(key: str, *, home: Path | None = None) -> dict[str, object]:
    profile = load_window_profile(key, home=home)
    return provision_box_mcp_artifacts(profile, home=home)
