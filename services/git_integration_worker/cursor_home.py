"""Per-dispatch HOME isolation for cursor-sdk bridge dispatches.

Each dispatch gets a private HOME with copied ``cli-config.json`` (identity),
XDG ``auth.json`` (credential), and user-layer Cursor settings (``rules/``, ``plugins/`` (ulg-ecosystem census skills),
``mcp.json``) so ``setting_sources=all`` matches the IDE Composer substrate.
The bridge subprocess inherits HOME via ``os.environ`` (see ``routes/cursor_sdk``
Branch B — ``launch_bridge`` snapshots ``os.environ`` at ``Popen``).
"""

from __future__ import annotations

import os
import pwd
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from universal_logging import get_logger

from services.git_integration_worker.cursor_seat_overlay import (
    apply_cursor_sdk_seat_overlay,
)

logger = get_logger(__name__)

CURSOR_CONFIG_DIRNAME = ".cursor"
CURSOR_IDENTITY_FILENAME = "cli-config.json"
CURSOR_MCP_FILENAME = "mcp.json"
CURSOR_USER_RULES_DIRNAME = "rules"
CURSOR_PLUGINS_DIRNAME = "plugins"
CURSOR_XDG_CONFIG_RELPATH = Path(".config") / "cursor"
CURSOR_CREDENTIAL_FILENAME = "auth.json"
GITCONFIG_FILENAME = ".gitconfig"
DISPATCH_GIT_EMAIL_DOMAIN = "dispatch.git-integration-worker"

def _passwd_home() -> Path:
    """Login-directory home from the passwd DB — immune to process ``HOME`` leaks."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir).expanduser()


def _default_dispatch_home_root() -> Path:
    """Module-default root — ``~`` expands against passwd, not process HOME."""
    raw = "~/.local/share/git-integration-worker/cursor-dispatch-homes"
    return (_passwd_home() / raw[2:].lstrip("/")).resolve()


def _resolve_dispatch_home_root(root: Path | None = None) -> Path:
    """Dispatch-home root for :func:`is_dispatch_home_path`."""
    if root is not None:
        return Path(root).expanduser().resolve()
    env = os.environ.get("CURSOR_DISPATCH_HOME_ROOT", "").strip()
    if env:
        if env.startswith("~"):
            return (_passwd_home() / env[2:].lstrip("/")).resolve()
        return Path(env).expanduser().resolve()
    return _DISPATCH_HOME_ROOT


_DISPATCH_HOME_ROOT = _default_dispatch_home_root()
_DEFAULT_DISPATCH_HOME_RETENTION_DAYS = int(
    os.environ.get("CURSOR_DISPATCH_HOME_RETENTION_DAYS", "14")
)


DEFAULT_REPO_VENV_RELPATH = Path(".venvs") / "universal"
REQUIRED_VENV_EXECUTABLES: tuple[str, ...] = ("python", "pytest", "ruff")
_CURSOR_AGENT_SHIM = Path(".local") / "bin" / "agent"


class CursorHomeConfigError(RuntimeError):
    """No usable cursor credential under the swapped HOME — fail closed pre-launch."""


class CursorVenvConfigError(RuntimeError):
    """Configured repo venv missing or incomplete — fail closed pre-launch."""


def is_dispatch_home_path(path: Path | str, *, root: Path | None = None) -> bool:
    """True when *path* is under the per-dispatch HOME root (contamination fingerprint)."""
    candidate = Path(path).expanduser().resolve()
    base = _resolve_dispatch_home_root(root)
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


def operator_real_home(*, explicit: Path | str | None = None) -> Path:
    """Operator home for credential/venv resolution — never a dispatch HOME.

    Prefer an explicit path when provided and not a dispatch home. Otherwise use
    the passwd login directory. ``os.environ['HOME']`` is consulted only as a
    last resort and rejected when it points under the dispatch-home root
    (GIW process contamination from a leaked overlay — CURSOR_VENV_CONFIG /
    agent-bus:6468).
    """
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not is_dispatch_home_path(path):
            return path.resolve() if path.exists() else path
        logger.warning(
            "operator_real_home: ignoring explicit dispatch-home path %s", path
        )
    passwd = _passwd_home()
    env_home = os.environ.get("HOME", "").strip()
    if env_home:
        env_path = Path(env_home).expanduser()
        if is_dispatch_home_path(env_path):
            logger.warning(
                "operator_real_home: process HOME=%s is a dispatch home; "
                "using passwd home %s",
                env_path,
                passwd,
            )
            return passwd
        # Prefer passwd when both exist and diverge — env may still be stale.
        if env_path.resolve() != passwd.resolve():
            logger.info(
                "operator_real_home: HOME=%s differs from passwd=%s; using passwd",
                env_path,
                passwd,
            )
        return passwd
    return passwd


def dispatch_home_path(dispatch_id: str, *, root: Path | None = None) -> Path:
    """``{root}/{dispatch_id}-home`` — root defaults to ``_DISPATCH_HOME_ROOT``."""
    base = root if root is not None else _DISPATCH_HOME_ROOT
    return base / f"{dispatch_id}-home"


def dispatch_git_identity(dispatch_id: str) -> tuple[str, str]:
    """Author/committer identity naming the dispatch for joinable lane commits."""
    name = f"cursor-sdk/{dispatch_id}"
    email = f"{dispatch_id}@{DISPATCH_GIT_EMAIL_DOMAIN}"
    return name, email


def dispatch_git_env_vars(dispatch_id: str) -> dict[str, str]:
    """Env overrides so git commits succeed when HOME is a dispatch overlay."""
    name, email = dispatch_git_identity(dispatch_id)
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


def seed_dispatch_git_identity(home: Path, dispatch_id: str) -> None:
    """Write ``~/.gitconfig`` under the per-dispatch HOME (never symlink)."""
    name, email = dispatch_git_identity(dispatch_id)
    gitconfig = home / GITCONFIG_FILENAME
    gitconfig.write_text(
        f"[user]\n\tname = {name}\n\temail = {email}\n",
        encoding="utf-8",
    )


def prune_stale_dispatch_homes(
    *,
    max_age_days: int | None = None,
    root: Path | None = None,
) -> int:
    """Delete dispatch HOME dirs older than *max_age_days* (default env/14).

    Each cursor-sdk dispatch copies credentials into a private HOME; these
    accumulate without bound. Prune at worker startup and via manual cleanup.
    """
    retention_days = (
        _DEFAULT_DISPATCH_HOME_RETENTION_DAYS
        if max_age_days is None
        else max_age_days
    )
    if retention_days < 1:
        return 0
    base = root if root is not None else _DISPATCH_HOME_ROOT
    if not base.is_dir():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for entry in base.iterdir():
        if not entry.is_dir() or not entry.name.endswith("-home"):
            continue
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
        except OSError as exc:
            logger.warning("dispatch_home prune skip %s: %s", entry, exc)
            continue
        try:
            shutil.rmtree(entry)
            removed += 1
        except OSError as exc:
            logger.warning("dispatch_home prune failed %s: %s", entry, exc)
    if removed:
        logger.info(
            "dispatch_home prune: removed=%d retention_days=%d root=%s cutoff=%s",
            removed,
            retention_days,
            base,
            datetime.fromtimestamp(cutoff, tz=UTC).isoformat(),
        )
    return removed


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
    real = operator_real_home(explicit=real_home)

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
    _copy_path_if_present(
        real_cursor / CURSOR_PLUGINS_DIRNAME,
        cursor_dir / CURSOR_PLUGINS_DIRNAME,
    )
    # Plugin parity with the IDE is wrong for the human-facing operator register:
    # a headless seat has no human reader. Swap it for the interagent counterpart.
    apply_cursor_sdk_seat_overlay(cursor_dir)
    seed_dispatch_git_identity(home, dispatch_id)
    return home


def resolve_repo_venv(
    *, real_home: Path | str | None = None, override: str | None = None
) -> Path:
    """Repo venv root: CURSOR_SDK_VENV_PATH override, else <real_home>/.venvs/universal.

    Uses :func:`operator_real_home` so a contaminated process ``HOME`` (dispatch
    overlay leak) cannot redirect the venv under a per-dispatch home.
    """
    if override is None:
        override = os.environ.get("CURSOR_SDK_VENV_PATH", "").strip() or None
    if override:
        return Path(override).expanduser()
    return operator_real_home(explicit=real_home) / DEFAULT_REPO_VENV_RELPATH


def validate_repo_venv(venv: Path) -> None:
    """Raise CursorVenvConfigError if the venv dir or a required executable is absent."""
    missing: list[str] = []
    if not venv.is_dir():
        missing.append(f"venv dir {venv}")
    for exe in REQUIRED_VENV_EXECUTABLES:
        if not (venv / "bin" / exe).exists():
            missing.append(f"bin/{exe}")
    if missing:
        raise CursorVenvConfigError(
            f"cursor-sdk repo venv invalid: {venv} — missing: {', '.join(missing)}"
        )


def _expand_real_home(real_home: Path | str | None) -> Path:
    return operator_real_home(explicit=real_home)


def is_cursor_agent_shim(path: Path) -> bool:
    """True when *path* resolves to cursor-agent, not grok's ``agent`` symlink."""
    if not path.exists():
        return False
    try:
        resolved = path.resolve()
    except OSError:
        return False
    resolved_str = str(resolved)
    if "cursor-agent" in resolved_str:
        return True
    if "/.grok/" in resolved_str or resolved.name.startswith("grok"):
        return False
    return resolved.name == "cursor-agent"


def operator_local_bin_for_cursor_agent(
    *, real_home: Path | str | None = None
) -> Path | None:
    """Return ``~/.local/bin`` when the operator has a verified cursor-agent shim."""
    shim = _expand_real_home(real_home) / _CURSOR_AGENT_SHIM
    if not is_cursor_agent_shim(shim):
        return None
    return shim.parent


def build_dispatch_path_prepend(
    repo_venv: Path,
    *,
    real_home: Path | str | None = None,
) -> str:
    """PATH prefix for bridge subprocess: repo venv, then cursor-agent shim dir.

    Grok's CLI installer also publishes an ``agent`` binary. Prepending the
    operator's verified ``~/.local/bin`` keeps cursor-agent ahead of grok when
    dispatch HOME is swapped and PATH would otherwise lack the operator shim.
    """
    segments = [str(repo_venv / "bin")]
    local_bin = operator_local_bin_for_cursor_agent(real_home=real_home)
    if local_bin is not None:
        segments.append(str(local_bin))
    return os.pathsep.join(segments)
