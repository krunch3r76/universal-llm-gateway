"""Git-tracking guards for cursor skill hardlinks and personal SOT paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _git_ls_files(repo_root: Path, path: Path) -> list[str]:
    rel = path.relative_to(repo_root).as_posix()
    out = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "--", rel],
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def _git_check_ignore(repo_root: Path, path: Path) -> bool:
    code = subprocess.call(
        ["git", "-C", str(repo_root), "check-ignore", "-q", str(path)],
        stderr=subprocess.DEVNULL,
    )
    return code == 0


def check_cursor_skills_gitignored(repo_root: Path) -> list[str]:
    """Fail if any ``.cursor/skills/**/SKILL.md`` is git-tracked."""
    problems: list[str] = []
    skills_root = repo_root / ".cursor" / "skills"
    if not skills_root.is_dir():
        return problems
    for skill_md in skills_root.glob("*/SKILL.md"):
        if _git_ls_files(repo_root, skill_md):
            problems.append(f"git-tracked cursor skill (must stay ignored): {skill_md}")
        elif not _git_check_ignore(repo_root, skill_md):
            problems.append(
                f"cursor skill not gitignored (verify .gitignore): {skill_md}"
            )
    return problems


def run_skill_git_guard(repo_root: Path) -> int:
    problems = check_cursor_skills_gitignored(repo_root)
    if not problems:
        return 0
    for line in problems:
        print(f"GIT-GUARD: {line}", flush=True)
    return 1
