"""Git subprocess helpers for linked worktrees across host/container path boundaries.

Lane-B worktrees record a host-absolute ``gitdir:`` in their ``.git`` file. MCP
life/code surfaces often run with ``PROJECT_ROOT=/data/project`` while that
pointer still names ``/mnt/torus/projects/...``. Plain ``git -C <worktree>``
then fails even though the checkout and main-repo worktree admin dir are
visible under the container mount.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_DEFAULT_TIMEOUT_S = 10.0


def _read_dot_git_pointer(worktree: Path) -> Path | None:
    dot = worktree / ".git"
    if not dot.is_file():
        return None
    text = dot.read_text(encoding="utf-8", errors="replace").strip()
    if not text.startswith("gitdir:"):
        return None
    raw = text[len("gitdir:") :].strip()
    return Path(raw) if raw else None


def host_source_repo() -> Path | None:
    raw = os.environ.get("GIT_INTEGRATION_SOURCE_REPO", "").strip()
    if not raw:
        return None
    return Path(raw).resolve()


def hub_project_root() -> Path:
    """Shared-checkout mount root — not the lane-bound ``tools._project_paths`` override."""
    if os.environ.get("ULG_HUB_PROJECT_ROOT", "").strip():
        return Path(os.environ["ULG_HUB_PROJECT_ROOT"]).resolve()
    try:
        from fs_roots import project_root_path

        return project_root_path()
    except ImportError:
        return Path(os.environ.get("PROJECT_ROOT", "/data/project")).resolve()


def container_source_repo(*, project_root: Path | None = None) -> Path:
    """Hub checkout directory under the shared mount (``universal-llm-gateway``)."""
    root = (project_root or hub_project_root()).resolve()
    ug = root / "universal-llm-gateway"
    marker = ug / "scripts" / "check-imports"
    if marker.exists():
        return ug
    if (root / ".git").exists():
        return root
    if (ug / ".git").exists() or ug.joinpath(".git").is_file():
        return ug
    return root


def translate_gitdir(gitdir: Path) -> Path:
    """Return a container-visible git administrative directory for *gitdir*."""
    if gitdir.exists():
        return gitdir.resolve()

    host = host_source_repo()
    container = container_source_repo()
    gitdir_s = str(gitdir)
    if host is not None:
        host_s = str(host)
        if gitdir_s.startswith(host_s):
            candidate = Path(gitdir_s.replace(host_s, str(container), 1))
            if candidate.exists():
                return candidate.resolve()

    parts = gitdir.parts
    for i, part in enumerate(parts):
        if part == ".git" and i + 2 < len(parts) and parts[i + 1] == "worktrees":
            wt_name = parts[i + 2]
            candidate = container / ".git" / "worktrees" / wt_name
            if candidate.exists():
                return candidate.resolve()
    return gitdir


def git_env_for_worktree(worktree: Path) -> dict[str, str] | None:
    """``GIT_DIR`` / ``GIT_WORK_TREE`` overrides when ``.git`` is a gitdir file."""
    worktree = worktree.resolve()
    pointer = _read_dot_git_pointer(worktree)
    if pointer is None:
        return None
    translated = translate_gitdir(pointer)
    if not translated.exists():
        return None
    return {
        "GIT_DIR": str(translated),
        "GIT_WORK_TREE": str(worktree),
    }


def git_run(
    repo_or_worktree: Path,
    args: list[str],
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
    check: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    extra = git_env_for_worktree(repo_or_worktree)
    if extra:
        env.update(extra)
    return subprocess.run(
        ["git", "-C", str(repo_or_worktree), *args],
        capture_output=True,
        timeout=timeout,
        check=check,
        env=env,
    )


def rev_parse_head(
    repo_or_worktree: Path,
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> tuple[str | None, str | None]:
    """Return ``(sha, error_reason)``; *error_reason* is set when *sha* is absent."""
    try:
        proc = git_run(repo_or_worktree, ["rev-parse", "HEAD"], timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"git rev-parse HEAD failed: {exc}"
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        return None, stderr or f"git rev-parse HEAD exited {proc.returncode}"
    sha = proc.stdout.decode("utf-8", errors="replace").strip()
    if not sha:
        return None, "git rev-parse HEAD returned empty"
    return sha, None
