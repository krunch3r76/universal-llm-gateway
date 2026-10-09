"""Install GIW forward-only git hooks beside existing repo-local hooks."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_GIT_TIMEOUT_S = 30.0
_HOOKS_DIR = Path(__file__).resolve().parent / "hooks"
_GIW_HOOK_NAMES = ("reference-transaction", "pre-push")


@dataclass(frozen=True, slots=True)
class GitHookInstallResult:
    """Outcome of :func:`ensure_git_hooks_installed`."""

    status: str  # installed | refused | skipped_hooks_path
    giw_hooks_dir: Path
    common_hooks_dir: Path | None = None
    detail: str = ""


def giw_git_hooks_dir() -> Path:
    """Absolute path to tracked ``reference-transaction`` and ``pre-push`` hooks."""
    return _HOOKS_DIR.resolve()


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"):
        env.pop(key, None)
    return env


def _git_capture(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
        env=_git_env(),
    )


def _common_hooks_dir(repo: Path) -> Path:
    proc = _git_capture(repo, "rev-parse", "--git-path", "hooks")
    if proc.returncode != 0:
        err = proc.stderr.strip() or "git rev-parse --git-path hooks failed"
        raise RuntimeError(err)
    raw = proc.stdout.strip()
    hooks = Path(raw)
    if not hooks.is_absolute():
        hooks = (repo / hooks).resolve()
    return hooks


def _symlink_matches(link: Path, target: Path) -> bool:
    return link.is_symlink() and link.resolve() == target.resolve()


def ensure_git_hooks_installed(source_repo: Path) -> GitHookInstallResult:
    """Symlink GIW hooks into ``$(git rev-parse --git-path hooks)``.

    Does not set ``core.hooksPath``. If ``core.hooksPath`` is already set, reports
    ``skipped_hooks_path`` and makes no changes. Refuses when a hook path exists
    but is not a symlink to the GIW source (does not overwrite).
    """
    repo = source_repo.resolve()
    giw_hooks = giw_git_hooks_dir()
    if not giw_hooks.is_dir():
        raise FileNotFoundError(f"GIW hooks directory missing: {giw_hooks}")

    hooks_path_proc = _git_capture(repo, "config", "--get", "core.hooksPath")
    if hooks_path_proc.returncode == 0 and hooks_path_proc.stdout.strip():
        detail = hooks_path_proc.stdout.strip()
        logger.warning(
            "git hooks install skipped; core.hooksPath already set repo=%s hooksPath=%s",
            repo,
            detail,
        )
        return GitHookInstallResult(
            status="skipped_hooks_path",
            giw_hooks_dir=giw_hooks,
            detail=detail,
        )

    hooks_dir = _common_hooks_dir(repo)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    for name in _GIW_HOOK_NAMES:
        target = giw_hooks / name
        if not target.is_file():
            raise FileNotFoundError(f"GIW hook missing: {target}")
        link = hooks_dir / name
        if link.exists() or link.is_symlink():
            if _symlink_matches(link, target):
                continue
            detail = f"{name} exists and is not a symlink to {target}"
            logger.warning(
                "git hooks install refused repo=%s hooks_dir=%s %s",
                repo,
                hooks_dir,
                detail,
            )
            return GitHookInstallResult(
                status="refused",
                giw_hooks_dir=giw_hooks,
                common_hooks_dir=hooks_dir,
                detail=detail,
            )
        link.symlink_to(target)

    logger.info(
        "git hooks installed repo=%s common_hooks_dir=%s giw_hooks_dir=%s",
        repo,
        hooks_dir,
        giw_hooks,
    )
    return GitHookInstallResult(
        status="installed",
        giw_hooks_dir=giw_hooks,
        common_hooks_dir=hooks_dir,
    )
