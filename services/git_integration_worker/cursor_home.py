"""Per-dispatch HOME isolation for cursor-sdk bridge dispatches.

Each dispatch gets a private HOME with copied ``cli-config.json`` (identity),
XDG ``auth.json`` (credential), ``~/.gateway/mcp.yaml`` (vortex token), and
user-layer Cursor settings (``rules/``, ``plugins/`` (ulg-ecosystem census skills),
``mcp.json``) so ``setting_sources=all`` matches the IDE Composer substrate.
The bridge subprocess receives HOME through the /usr/bin/env argv shim
built in cursor_sdk_bridge_launch — never through os.environ.
"""

from __future__ import annotations

import json
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
# Copied operator mcp.json registers these as HTTP/OAuth names. Local SDK runs
# reject mcp_auth, so the copies stay discovery-red. SDK injects the same
# code-mount via build_mcp_servers (user-vortex + vortex-code).
_COPIED_OAUTH_MCP_SERVERS = frozenset({"vortex-code", "vortex-life"})


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


def observed_home_kind(path: Path | str, *, root: Path | None = None) -> str:
    """Return ``dispatch`` or ``operator`` for an observed home path.

    A2 (honest-observability-class): name the scope of a HOME read so a
    dispatch-scoped miss cannot be reported as an operator-global negative.
    This classifies the path that was read — it does not retarget the read.
    """
    return "dispatch" if is_dispatch_home_path(path, root=root) else "operator"


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


def dispatch_git_identity(
    dispatch_id: str,
    *,
    thread_id: str | None = None,
) -> tuple[str, str]:
    """Author/committer identity naming the lane (or dispatch) for joinable commits."""
    label = f"lane-{thread_id}" if thread_id else dispatch_id
    name = f"cursor-sdk/{label}"
    email = f"{dispatch_id}@{DISPATCH_GIT_EMAIL_DOMAIN}"
    return name, email


def dispatch_git_env_vars(
    dispatch_id: str,
    *,
    thread_id: str | None = None,
) -> dict[str, str]:
    """Env overrides so git commits succeed when HOME is a dispatch overlay."""
    name, email = dispatch_git_identity(dispatch_id, thread_id=thread_id)
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
        _DEFAULT_DISPATCH_HOME_RETENTION_DAYS if max_age_days is None else max_age_days
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


def _strip_copied_oauth_mcp_servers(mcp_json: Path) -> None:
    """Remove vortex-code/vortex-life from a copied HOME mcp.json.

    Operator mcp.json is IDE-parity (stdio bridge entries whose MCP_URL
    still trips Cursor's OAuth/mcp_auth path). Leaving those names in a
    dispatch HOME makes GetMcpTools report serverStatus=error + mcp_auth
    only, which local SDK runs then reject. Callers: setup_cursor_dispatch_home
    after the mcp.json copy. Mutates the file in place; no-op if absent,
    unreadable, or already empty of those keys.
    """
    if not mcp_json.is_file():
        return
    try:
        payload = json.loads(mcp_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("dispatch_home: skip mcp.json strip; unreadable %s", mcp_json)
        return
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict):
        return
    stripped = {k: v for k, v in servers.items() if k not in _COPIED_OAUTH_MCP_SERVERS}
    if stripped == servers:
        return
    payload["mcpServers"] = stripped
    mcp_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _link_operator_venv(home: Path, real: Path) -> None:
    """Point dispatch ``$HOME/.venvs`` at the operator interpreter — never copy it.

    Agents follow the python-universal-venv rule and invoke
    ``$HOME/.venvs/universal/bin/python``. Under a swapped dispatch HOME that
    path is missing (exit 127, ``auto-787c6b89be1f``). The interpreter stays at
    the operator path; this is a pointer, not a second root.
    """
    src = real / DEFAULT_REPO_VENV_RELPATH.parent
    dst = home / DEFAULT_REPO_VENV_RELPATH.parent
    if not src.is_dir():
        return
    if dst.is_symlink():
        try:
            if dst.resolve() == src.resolve():
                return
        except OSError:
            pass
        logger.warning(
            "dispatch_home: venv pointer %s exists and does not match %s", dst, src
        )
        return
    if dst.exists():
        logger.warning("dispatch_home: venv pointer skipped; %s already exists", dst)
        return
    try:
        dst.symlink_to(src, target_is_directory=True)
    except OSError as exc:
        logger.warning(
            "dispatch_home: venv pointer skipped %s -> %s: %s", dst, src, exc
        )


def setup_cursor_dispatch_home(
    dispatch_id: str,
    *,
    real_home: Path | str | None = None,
    root: Path | None = None,
) -> Path:
    """Create dispatch HOME and seed cursor credentials by copy (never symlink).

    Copies identity, credential, user rules, plugins, mcp.yaml, and mcp.json
    from the operator home, then strips copied ``vortex-code``/``vortex-life``
    entries so Cursor does not classify those names as OAuth on this seat.
    ``$HOME/.venvs`` is a pointer at the operator venv, not a copied
    interpreter root.
    """
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
    _strip_copied_oauth_mcp_servers(cursor_dir / CURSOR_MCP_FILENAME)
    _copy_path_if_present(
        real_cursor / CURSOR_PLUGINS_DIRNAME,
        cursor_dir / CURSOR_PLUGINS_DIRNAME,
    )
    # Vortex stdio bridge reads ~/.gateway/mcp.yaml via Path.home(). Omitting
    # this copy is the cursor-sdk discovery outage (empty tools / 401).
    _copy_path_if_present(
        real / ".gateway" / "mcp.yaml",
        home / ".gateway" / "mcp.yaml",
    )
    # Plugin parity with the IDE is wrong for the human-facing operator register:
    # a headless seat has no human reader. Swap it for the interagent counterpart.
    apply_cursor_sdk_seat_overlay(cursor_dir)
    seed_dispatch_git_identity(home, dispatch_id)
    _link_operator_venv(home, real)
    return home


def resolve_repo_venv(*, real_home: Path | str | None = None) -> Path:
    """Return the mandatory universal venv used by every SDK dispatch.

    The worker may create a private HOME for Cursor credentials, but that HOME
    must never become the interpreter/configuration root.  An alternate
    ``CURSOR_SDK_VENV_PATH`` is rejected instead of allowing a dispatch to run
    under an unapproved interpreter.
    """
    required = operator_real_home(explicit=real_home) / DEFAULT_REPO_VENV_RELPATH
    configured = os.environ.get("CURSOR_SDK_VENV_PATH", "").strip()
    if configured:
        configured_path = Path(configured).expanduser().resolve()
        if configured_path != required.resolve():
            raise CursorVenvConfigError(
                "cursor-sdk dispatch requires the universal venv at "
                f"{required}; CURSOR_SDK_VENV_PATH={configured_path} is not allowed"
            )
    return required


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
