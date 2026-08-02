"""Git-tracking guards for cursor skill hardlinks and personal SOT paths.

Operator bind 2026-08-01: the engineering guidance substrate is tracked, so a
blanket "no cursor skill may be tracked" rule no longer expresses the boundary.
What must hold instead:

1. Life-domain skills stay untracked — they carry real names, probate/APN
   numbers, account identifiers, and psych material.
2. A tracked skill must not be hardlinked into an untracked tree, or an edit
   through the ignored path would silently mutate tracked content.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

#: Skill slugs under ``.cursor/skills/`` that are about real personal matters
#: rather than engineering process. Mirrored by the ``.gitignore`` block that
#: re-excludes them; both must be updated together.
PERSONAL_SKILL_SLUGS = frozenset(
    {
        "tax",
        "w2-ingestion",
        "lawyer-stance",
        "lawyer-stance-code",
        "legal-opinion-corpus-ingestion",
        "case-evidence-retrieval",
        "corpus-cross-reference-discipline",
        "psych-framework-counsel",
        "crypto-trading-research",
        "messages-check",
    }
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
    """Fail if a personal skill is exposed, or a tracked skill is hardlinked out."""
    problems: list[str] = []
    skills_root = repo_root / ".cursor" / "skills"
    if not skills_root.is_dir():
        return problems
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        tracked = bool(_git_ls_files(repo_root, skill_md))
        if skill_md.parent.name in PERSONAL_SKILL_SLUGS:
            if tracked:
                problems.append(f"personal skill is git-tracked: {skill_md}")
            elif not _git_check_ignore(repo_root, skill_md):
                problems.append(
                    f"personal skill not gitignored (verify .gitignore): {skill_md}"
                )
        elif tracked and skill_md.stat().st_nlink > 1:
            problems.append(
                "tracked skill is hardlinked — an edit through the other path would "
                f"mutate tracked content invisibly: {skill_md}"
            )
    return problems


def run_skill_git_guard(repo_root: Path) -> int:
    problems = check_cursor_skills_gitignored(repo_root)
    if not problems:
        return 0
    for line in problems:
        print(f"GIT-GUARD: {line}", flush=True)
    return 1
