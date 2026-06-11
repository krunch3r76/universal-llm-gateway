"""Per-dispatch HOME isolation for cursor-sdk bridge dispatches.

Each dispatch gets a private HOME with copied ``cli-config.json`` (identity),
XDG ``auth.json`` (credential), and user-layer Cursor settings (``rules/``,
``mcp.json``) so ``setting_sources=all`` matches the IDE Composer substrate.
The bridge subprocess inherits HOME via ``os.environ`` (see ``routes/cursor_sdk``
Branch B — ``launch_bridge`` snapshots ``os.environ`` at ``Popen``).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

CURSOR_CONFIG_DIRNAME = ".cursor"
CURSOR_IDENTITY_FILENAME = "cli-config.json"
CURSOR_MCP_FILENAME = "mcp.json"
CURSOR_USER_RULES_DIRNAME = "rules"
CURSOR_XDG_CONFIG_RELPATH = Path(".config") / "cursor"
CURSOR_CREDENTIAL_FILENAME = "auth.json"

_DISPATCH_HOME_ROOT = Path(
    os.environ.get(
        "CURSOR_DISPATCH_HOME_ROOT",
        "~/.local/share/git-integration-worker/cursor-dispatch-homes",
    )
).expanduser()


class CursorHomeConfigError(RuntimeError):
    """No usable cursor credential under the swapped HOME — fail closed pre-launch."""


def dispatch_home_path(dispatch_id: str, *, root: Path | None = None) -> Path:
    """``{root}/{dispatch_id}-home`` — root defaults to ``_DISPATCH_HOME_ROOT``."""
    base = root if root is not None else _DISPATCH_HOME_ROOT
    return base / f"{dispatch_id}-home"


def _copy_path_if_present(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=False)
        return
    shutil.copy2(src, dst)


def setup_cursor_dispatch_home(
    dispatch_id: str,
    *,
    real_home: Path | str | None = None,
    root: Path | None = None,
) -> Path:
    """Create dispatch HOME and seed cursor credentials by copy (never symlink)."""
    home = dispatch_home_path(dispatch_id, root=root)
    real = Path(real_home) if real_home else Path(os.environ.get("HOME") or "~")
    real = real.expanduser()

    cursor_dir = home / CURSOR_CONFIG_DIRNAME
    cursor_dir.mkdir(parents=True, exist_ok=True)
    real_identity = real / CURSOR_CONFIG_DIRNAME / CURSOR_IDENTITY_FILENAME
    if real_identity.exists():
        shutil.copy2(real_identity, cursor_dir / CURSOR_IDENTITY_FILENAME)
    else:
        logger.warning(
            "dispatch_home[%s]: identity %s absent at %s",
            dispatch_id,
            CURSOR_IDENTITY_FILENAME,
            real_identity,
        )

    xdg_dir = home / CURSOR_XDG_CONFIG_RELPATH
    xdg_dir.mkdir(parents=True, exist_ok=True)
    real_cred = real / CURSOR_XDG_CONFIG_RELPATH / CURSOR_CREDENTIAL_FILENAME
    have_api_key = bool(os.environ.get("CURSOR_API_KEY"))
    if real_cred.exists():
        shutil.copy2(real_cred, xdg_dir / CURSOR_CREDENTIAL_FILENAME)
    elif not have_api_key:
        raise CursorHomeConfigError(
            f"dispatch[{dispatch_id}]: no credential — real {real_cred} absent "
            f"and CURSOR_API_KEY unset; bridge would fail 'Authentication required'"
        )

    real_cursor = real / CURSOR_CONFIG_DIRNAME
    _copy_path_if_present(
        real_cursor / CURSOR_USER_RULES_DIRNAME,
        cursor_dir / CURSOR_USER_RULES_DIRNAME,
    )
    _copy_path_if_present(
        real_cursor / CURSOR_MCP_FILENAME,
        cursor_dir / CURSOR_MCP_FILENAME,
    )
    return home
