"""Hub-land scope from commissioner packet prose and lane-B ref reachability."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

_GIT_TIMEOUT_S = 10.0
CURSOR_SDK_HUB_MASTER_REF_ENV = "CURSOR_SDK_HUB_MASTER_REF"
_DEFAULT_HUB_MASTER_REF = "refs/heads/master"

_LAND_ONLY_NOT_SCOPED_OUT_RE = re.compile(
    r"files_expected\s*:\s*none\s*[—–-]\s*land\s+only\b",
    re.IGNORECASE,
)
_HUB_LAND_SCOPED_OUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"do\s+not\s+hub[- ]land", re.IGNORECASE),
    re.compile(r"out-of-scope\s*:\s*[^\n]*\bhub\s+land\b", re.IGNORECASE),
    re.compile(r"out\.of\.scope\s*:\s*[^\n]*\bhub\s+land\b", re.IGNORECASE),
    re.compile(r"out-of-scope[^\n]*\bhub\s+land\b", re.IGNORECASE),
    re.compile(r"¬\s*hub[- ]land", re.IGNORECASE),
)


def packet_hub_land_scoped_out(prose: str | None) -> bool:
    """True when the packet explicitly excludes hub land from this dispatch's scope.

    ``files_expected: none — land only`` is **not** scoped-out (land is the work).
    """
    if not prose:
        return False
    if _LAND_ONLY_NOT_SCOPED_OUT_RE.search(prose):
        return False
    return any(pat.search(prose) for pat in _HUB_LAND_SCOPED_OUT_PATTERNS)


def resolve_hub_master_ref(*, hub_repo: Path | None = None) -> str:
    """Hub ``master`` ref for landed probes (override via ``CURSOR_SDK_HUB_MASTER_REF``)."""
    _ = hub_repo
    return os.environ.get(CURSOR_SDK_HUB_MASTER_REF_ENV, _DEFAULT_HUB_MASTER_REF)


def resolve_hub_git_repo(from_repo: Path) -> Path:
    """Return a hub checkout path for master ancestry probes from a lane worktree."""
    repo = from_repo.resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return repo
    if proc.returncode != 0:
        return repo
    lines = proc.stdout.splitlines()
    first_worktree: Path | None = None
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not line.startswith("worktree "):
            idx += 1
            continue
        wt_path = Path(line.split(" ", 1)[1].strip())
        if first_worktree is None:
            first_worktree = wt_path
        idx += 1
        while idx < len(lines) and not lines[idx].startswith("worktree "):
            if lines[idx].startswith("branch refs/heads/master"):
                return wt_path
            idx += 1
    return first_worktree or repo


def commit_is_ancestor_of_hub_master(
    hub_repo: Path,
    sha: str,
    *,
    master_ref: str | None = None,
) -> bool | None:
    """True/False when hub master resolves; ``None`` when the ref is absent."""
    normalized = (sha or "").strip().lower()
    if not normalized:
        return None
    git_repo = resolve_hub_git_repo(hub_repo)
    ref = master_ref or resolve_hub_master_ref(hub_repo=git_repo)
    try:
        verify = subprocess.run(
            [
                "git",
                "-C",
                str(git_repo),
                "rev-parse",
                "--quiet",
                "--verify",
                ref,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if verify.returncode != 0:
        return None
    try:
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(git_repo),
                "merge-base",
                "--is-ancestor",
                normalized,
                ref,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return ancestor.returncode == 0


def commit_reachable_on_branch_ref(
    source_repo: Path,
    *,
    branch_name: str,
    sha: str,
) -> bool:
    """True when *sha* exists and lies on ``refs/heads/<branch_name>`` history."""
    normalized = (sha or "").strip().lower()
    if not normalized:
        return False
    repo = source_repo.resolve()
    try:
        verify = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "rev-parse",
                "--quiet",
                "--verify",
                f"{normalized}^{{commit}}",
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if verify.returncode != 0:
        return False
    ref = f"refs/heads/{branch_name}"
    try:
        ancestor = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "merge-base",
                "--is-ancestor",
                normalized,
                ref,
            ],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return ancestor.returncode == 0
