"""Git-tracking guards for cursor skill hardlinks and personal SOT paths."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from claude_bundles.resolver import (
    CORTEX_SOT_ROOT,
    CURSOR_INDEXED_SLUGS,
    _strip_pointer_fences,
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


def cortex_body_drift_slug(slug: str, repo_root: Path) -> str | None:
    """Return *slug* when cortex mount body differs from ``resolve_sot`` (fence-normalised)."""
    cortex_path = CORTEX_SOT_ROOT / f"{slug}.md"
    if not cortex_path.is_file():
        return None
    try:
        resolved_path, _ = resolve_sot(slug, repo_root)
    except FileNotFoundError:
        return None
    cortex_body = _strip_pointer_fences(cortex_path.read_text(encoding="utf-8"))
    resolved_body = _strip_pointer_fences(resolved_path.read_text(encoding="utf-8"))
    if cortex_body == resolved_body:
        return None
    return slug


def cortex_body_drift_remediation(slug: str, repo_root: Path) -> str:
    cortex_path = CORTEX_SOT_ROOT / f"{slug}.md"
    cursor_path = repo_root / ".cursor" / "skills" / slug / "SKILL.md"
    return f'cp "{cortex_path}" "{cursor_path}" && make claude-bundles'


def run_cortex_cursor_body_drift_check(
    repo_root: Path, slugs: list[str]
) -> int:
    """Fail-loud when cortex SOT mount diverges from the resolved ``.cursor`` copy."""
    drifting = [
        slug for slug in slugs if cortex_body_drift_slug(slug, repo_root) is not None
    ]
    for slug in drifting:
        print(f"DRIFT (cortex SOT ≠ .cursor): {slug}", file=sys.stderr)
        print(
            f"Remediation: {cortex_body_drift_remediation(slug, repo_root)}",
            file=sys.stderr,
        )
    return 1 if drifting else 0


def check_cortex_sot_only_slugs(repo_root: Path) -> list[str]:
    """Cortex-mount SOT slugs (``sot: cortex``) must resolve under cortex root, not tracked docs.

    Body drift for all bundle slugs is enforced separately by
    ``run_cortex_cursor_body_drift_check`` (not gated on ``sot: cortex`` frontmatter).
    """
    problems: list[str] = []
    slugs = cortex_sot_only_slugs()
    if not slugs:
        return problems
    if not CORTEX_SOT_ROOT.is_dir():
        problems.append(
            f"cortex SOT mount missing ({CORTEX_SOT_ROOT}) — "
            "cannot verify cortex SOT slugs"
        )
        return problems
    cortex_root = CORTEX_SOT_ROOT.resolve()
    for slug in sorted(slugs):
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
