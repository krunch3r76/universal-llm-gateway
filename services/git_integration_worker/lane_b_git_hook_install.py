"""Install GIW tracked git hooks on the hub ``source_repo`` via ``core.hooksPath``."""

from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT_S = 30.0
_HOOKS_DIR = Path(__file__).resolve().parent / "hooks"


def giw_git_hooks_dir() -> Path:
    """Absolute path to tracked ``reference-transaction`` and ``pre-push`` hooks."""
    return _HOOKS_DIR.resolve()


def ensure_git_hooks_installed(source_repo: Path) -> Path:
    """Point ``core.hooksPath`` at GIW hooks when missing or stale. Returns hooks dir."""
    repo = source_repo.resolve()
    hooks = giw_git_hooks_dir()
    if not hooks.is_dir():
        raise FileNotFoundError(f"GIW hooks directory missing: {hooks}")

    proc = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    current = proc.stdout.strip() if proc.returncode == 0 else ""
    desired = str(hooks)
    if current != desired:
        set_proc = subprocess.run(
            ["git", "-C", str(repo), "config", "core.hooksPath", desired],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
        if set_proc.returncode != 0:
            err = set_proc.stderr.strip() or "git config core.hooksPath failed"
            raise RuntimeError(err)
    return hooks
