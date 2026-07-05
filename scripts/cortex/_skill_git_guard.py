"""Git-tracking guards for cursor skill hardlinks and personal SOT paths."""

from __future__ import annotations

import subprocess
from pathlib import Path

from claude_bundles.resolver import (
    CORTEX_SOT_ROOT,
    CURSOR_INDEXED_SLUGS,
    cortex_sot_only_slugs,
    resolve_sot,
)


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


def check_cortex_sot_only_slugs(repo_root: Path) -> list[str]:
    """Cortex-mount SOT slugs (``sot: cortex``) must resolve under cortex root, not tracked docs."""
    problems: list[str] = []
    if not CORTEX_SOT_ROOT.is_dir():
        problems.append(
            f"cortex SOT mount missing ({CORTEX_SOT_ROOT}) — "
            "cannot verify cortex SOT slugs"
        )
        return problems
    cortex_root = CORTEX_SOT_ROOT.resolve()
    for slug in sorted(cortex_sot_only_slugs()):
        try:
            sot_path, label = resolve_sot(slug, repo_root)
        except FileNotFoundError as exc:
            msg = str(exc)
            if slug not in CURSOR_INDEXED_SLUGS and "no SOT" in msg:
                continue
            problems.append(f"{slug}: {exc}")
            continue
        resolved = sot_path.resolve()
        if not resolved.is_relative_to(cortex_root):
            problems.append(
                f"{slug}: cortex SOT-only but resolved={label} ({resolved})"
            )
            continue
        try:
            resolved.relative_to(repo_root.resolve())
        except ValueError:
            continue
        if _git_ls_files(repo_root, resolved):
            problems.append(
                f"{slug}: personal SOT is git-tracked ({resolved}) — move to cortex"
            )
    return problems


def run_skill_git_guard(repo_root: Path) -> int:
    problems = check_cursor_skills_gitignored(repo_root)
    problems.extend(check_cortex_sot_only_slugs(repo_root))
    if not problems:
        return 0
    for line in problems:
        print(f"GIT-GUARD: {line}", flush=True)
    return 1
