"""Polarity proof gates for wt_baseline → closeout path attribution (6341 L1).

Callers: ``changed_paths`` admits a files_* bucket only after ``prove_polarity``.
Unproved claims become ``capture:polarity_unproved:{path}`` — no failed-claim suffix.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

ClaimedOp = Literal["deleted", "created", "modified"]

_DELETION_PORCELAIN_CODES = frozenset({" D", "D ", "AD"})


def list_git_deleted_paths(source_repo: Path) -> frozenset[str]:
    """Paths git reports deleted in the index/worktree (conjunct 4 helper)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(source_repo), "ls-files", "--deleted", "-z"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return frozenset()
    return frozenset(
        chunk.decode("utf-8", errors="replace")
        for chunk in proc.stdout.split(b"\0")
        if chunk
    )


def _tracked_at_commit(source_repo: Path, commit: str, path: str) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(source_repo), "cat-file", "-e", f"{commit}:{path}"],
            capture_output=True,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return False
    return True


def _present_at_admit(
    path: str,
    baseline_codes: dict[str, str],
    baseline_hashes: dict[str, str],
    *,
    admit_head: str | None = None,
    source_repo: Path | None = None,
) -> bool:
    if path in baseline_codes or path in baseline_hashes:
        return True
    if admit_head is not None and source_repo is not None:
        return _tracked_at_commit(source_repo, admit_head, path)
    return False


def git_concurs_deleted(
    path: str,
    current_porcelain: dict[str, str],
    git_deleted_paths: frozenset[str],
) -> bool:
    """True when porcelain or ``git ls-files --deleted`` lists *path* as gone."""
    code = current_porcelain.get(path)
    if code is not None and code in _DELETION_PORCELAIN_CODES:
        return True
    return path in git_deleted_paths


def prove_polarity(
    *,
    claimed: ClaimedOp,
    path: str,
    source_repo: Path,
    baseline_codes: dict[str, str],
    baseline_hashes: dict[str, str],
    current_porcelain: dict[str, str],
    current_hash: str | None,
    git_deleted_paths: frozenset[str],
    admit_head: str | None = None,
) -> bool:
    """§5 conjuncts 2–4: gate a claimed files_* polarity before bucket admission."""
    repo_path = source_repo / path
    if claimed == "deleted":
        if repo_path.exists():
            return False
        if not _present_at_admit(
            path,
            baseline_codes,
            baseline_hashes,
            admit_head=admit_head,
            source_repo=source_repo,
        ):
            return False
        return git_concurs_deleted(path, current_porcelain, git_deleted_paths)
    if claimed == "created":
        if not repo_path.exists():
            return False
        admit_code = baseline_codes.get(path)
        if admit_code is not None and not admit_code.startswith("?"):
            return False
        return True
    if not repo_path.exists():
        return False
    admit_hash = baseline_hashes.get(path)
    if admit_hash is not None:
        return current_hash is not None and current_hash != admit_hash
    if path in baseline_codes:
        return False
    cur = current_porcelain.get(path)
    return cur is not None and not cur.startswith("?")


def polarity_deviation_token(path: str) -> str:
    """Name the unproved path only — do not suffix a failed polarity claim.

    ``prove_polarity`` already returned False. Repeating ``:{claimed}``
    answers confidently about a polarity this surface could not prove.
    """
    return f"capture:polarity_unproved:{path}"
